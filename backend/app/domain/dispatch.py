"""Rule-based dispatch: offer each pickup to the nearest available rider
(engine-design.md section 2). Every round is logged in full so a learned
router can later be trained, and judged, against it."""

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import jobs
from app.config import Settings, get_settings
from app.db import Conn
from app.domain import events, notify, payments, pickups
from app.domain.pickups import Event, Status

# Statuses in which a rider is busy with a pickup. Waiting for the household's
# PIN doesn't count: the rider has already left.
ACTIVE_JOB_STATUSES = ("assigned", "arrived", "location_issue", "verifying", "rider_review")

# How far off a location might be, by how it was reported.
UNCERTAINTY_M = {"gps": 0, "landmark": 300, "arrival": 300, "zone": 800}

# The rider-menu option for job offers, quoted in offer SMS.
USSD_OFFER_OPTION = "3"
# Self-reported location changes a rider may make in an hour (threat model T9).
SELF_REPORTS_PER_HOUR = 6

MAX_MISSED_OFFERS = 3
UNREACHABLE_FOR = timedelta(minutes=30)


class OfferNotAvailable(Exception):
    pass


class OfferNotFound(LookupError):
    pass


class SelfReportNotAllowed(Exception):
    """App riders are located by GPS only, so they can't claim a place by menu."""


class TooManyLocationChanges(Exception):
    pass


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Candidate:
    rider_id: UUID
    user_id: UUID
    channel: str
    distance_m: int
    location_source: str
    location_age_s: int
    jobs_today: int
    idle_s: int

    @property
    def score(self) -> float:
        return self.distance_m + UNCERTAINTY_M[self.location_source]


async def ranked_candidates(
    conn: Conn, pickup_id: UUID, lat: float, lng: float, settings: Settings
) -> list[Candidate]:
    cur = await conn.execute(
        """
        -- A fix's age is the older of when it was taken and when it reached us:
        -- a rider can neither backdate a stale fix nor upload an old one as new.
        -- App riders count only by GPS, so a menu self-report can't move them.
        with latest as (
            select distinct on (l.rider_id) l.rider_id, l.lat, l.lng, l.source,
                   least(l.reported_at, l.received_at) as taken_at
              from engine.rider_locations l
              join engine.riders lr on lr.id = l.rider_id
             where l.received_at > now() - make_interval(secs => %(self_fresh)s)
               and (l.source = 'gps' or lr.channel = 'ussd')
             order by l.rider_id, l.received_at desc, l.reported_at desc
        )
        select r.id as rider_id, r.user_id, r.channel, l.lat, l.lng, l.source,
               extract(epoch from now() - l.taken_at)::int as location_age_s,
               (select count(*) from engine.pickup_requests p
                 where p.assigned_rider_id = r.id and p.assigned_at >= date_trunc('day', now()))
                 as jobs_today,
               extract(epoch from now() - coalesce(
                   (select max(p.assigned_at) from engine.pickup_requests p
                     where p.assigned_rider_id = r.id), r.duty_since, now()))::int as idle_s
          from engine.riders r
          join engine.users u on u.id = r.user_id
          join latest l on l.rider_id = r.id
         where r.on_duty and u.status = 'active'
           and (r.unreachable_until is null or r.unreachable_until < now())
           and l.taken_at > now() - make_interval(secs =>
                 case when l.source = 'gps' then %(gps_fresh)s else %(self_fresh)s end)
           and (select count(*) from engine.pickup_requests p
                 where p.assigned_rider_id = r.id and p.status = any(%(active)s))
               < r.max_active_jobs
           and not exists (select 1 from engine.dispatch_offers o
                            where o.rider_id = r.id and o.response is null)
           and not exists (select 1 from engine.dispatch_offers o
                            where o.rider_id = r.id and o.pickup_id = %(pickup_id)s)
        """,
        {
            "gps_fresh": settings.gps_fresh_s,
            "self_fresh": settings.self_report_fresh_s,
            "active": list(ACTIVE_JOB_STATUSES),
            "pickup_id": pickup_id,
        },
    )
    found = []
    for row in await cur.fetchall():
        distance = round(haversine_m(lat, lng, row["lat"], row["lng"]))
        if distance <= settings.dispatch_radius_m:
            found.append(
                Candidate(
                    rider_id=row["rider_id"],
                    user_id=row["user_id"],
                    channel=row["channel"],
                    distance_m=distance,
                    location_source=row["source"],
                    location_age_s=row["location_age_s"],
                    jobs_today=row["jobs_today"],
                    idle_s=row["idle_s"],
                )
            )
    return sorted(found, key=lambda c: (c.score, c.jobs_today, -c.idle_s))


@jobs.handler("dispatch.next")
async def dispatch_next(conn: Conn, payload: dict) -> None:
    """Offer the pickup to the best rider, or try again later. A no-op unless
    the pickup is waiting for dispatch, so duplicate jobs are harmless."""
    settings = get_settings()
    pickup_id = UUID(payload["pickup_id"])
    cur = await conn.execute(
        """
        select p.status, p.version, p.lat, p.lng, p.fee_pesewas, p.first_offered_at,
               coalesce(p.paid_at, p.requested_at) as dispatch_since, h.user_id as household_user_id
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup["status"] != Status.PENDING_DISPATCH:
        return

    if datetime.now(UTC) - pickup["dispatch_since"] > timedelta(seconds=settings.dispatch_window_s):
        await _end_window(conn, pickup_id)
        return

    ranked = await ranked_candidates(conn, pickup_id, pickup["lat"], pickup["lng"], settings)
    await events.record(
        conn,
        "dispatch.round",
        actor_type="system",
        pickup_id=pickup_id,
        payload={"candidates": [_jsonable(c) for c in ranked]},
    )
    if not ranked:
        await jobs.enqueue(
            conn,
            "dispatch.next",
            {"pickup_id": str(pickup_id)},
            run_at=datetime.now(UTC) + timedelta(seconds=settings.dispatch_retry_s),
        )
        return

    best = ranked[0]
    ttl = settings.offer_ttl_pwa_s if best.channel == "pwa" else settings.offer_ttl_ussd_s
    cur = await conn.execute(
        """
        insert into engine.dispatch_offers
            (pickup_id, rider_id, rank, distance_m, location_source, channel, expires_at)
        values (%s, %s, 1, %s, %s, %s, now() + make_interval(secs => %s))
        returning id, expires_at
        """,
        (pickup_id, best.rider_id, best.distance_m, best.location_source, best.channel, ttl),
    )
    offer = await cur.fetchone()
    await pickups.apply(
        conn,
        pickup_id,
        Event.OFFER_SENT,
        expected_version=pickup["version"],
        actor_type="system",
        changes={} if pickup["first_offered_at"] else {"first_offered_at": pickups.NOW},
        payload={"offer_id": str(offer["id"]), "rider_id": str(best.rider_id)},
    )
    await jobs.enqueue(
        conn,
        "offer.expire",
        {"offer_id": str(offer["id"])},
        run_at=offer["expires_at"],
        dedupe_key=f"offer-expire:{offer['id']}",
    )
    earning = pickup["fee_pesewas"] * settings.rider_share_percent // 100
    if best.channel == "ussd":
        how = f"Dial {settings.ussd_code or 'the Imfucuza code'}, choose {USSD_OFFER_OPTION}"
    else:
        how = "Accept in the app"
    await notify.queue_sms(
        conn,
        user_id=best.user_id,
        pickup_id=pickup_id,
        offer_id=offer["id"],
        template="offer",
        text=(
            f"Imfucuza job: refuse pickup {best.distance_m / 1000:.1f} km away, "
            f"you earn GHS {earning / 100:.2f}. {how} within {ttl // 60 or 1} min."
        ),
    )


@jobs.handler("dispatch.expire")
async def _window_job(conn: Conn, payload: dict) -> None:
    """The dispatch window's own timer, so a paid pickup can't wait in escrow
    forever if its dispatch.next chain ever breaks."""
    await _end_window(conn, UUID(payload["pickup_id"]))


async def _end_window(conn: Conn, pickup_id: UUID) -> None:
    await withdraw_open_offer(conn, pickup_id)
    cur = await conn.execute(
        """
        select p.status, p.version, h.user_id as household_user_id
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup["status"] not in (Status.PENDING_DISPATCH, Status.OFFERED):
        return
    await pickups.apply(
        conn,
        pickup_id,
        Event.DISPATCH_WINDOW_ENDED,
        expected_version=pickup["version"],
        actor_type="system",
    )
    refunded = await payments.start_refund(conn, pickup_id)
    await notify.queue_sms(
        conn,
        user_id=pickup["household_user_id"],
        pickup_id=pickup_id,
        template="dispatch_expired",
        text="Imfucuza: sorry, no rider could take your pickup today."
        + (" Your payment is being refunded." if refunded else ""),
    )


async def accept(
    conn: Conn, offer_id: UUID, *, rider_id: UUID, user_id: UUID
) -> pickups.Transitioned:
    offer = await _open_offer(conn, offer_id, rider_id)
    if offer["is_expired"]:
        raise OfferNotAvailable("offer has expired")
    await conn.execute(
        """
        update engine.dispatch_offers set response = 'accepted', responded_at = now() where id = %s
        """,
        (offer_id,),
    )
    await conn.execute("update engine.riders set missed_offers = 0 where id = %s", (rider_id,))
    version = await _pickup_version(conn, offer["pickup_id"])
    return await pickups.apply(
        conn,
        offer["pickup_id"],
        Event.OFFER_ACCEPTED,
        expected_version=version,
        actor_type="rider",
        actor_id=user_id,
        changes={"assigned_rider_id": rider_id, "assigned_at": pickups.NOW},
        payload={"offer_id": str(offer_id)},
    )


async def decline(conn: Conn, offer_id: UUID, *, rider_id: UUID, user_id: UUID) -> None:
    offer = await _open_offer(conn, offer_id, rider_id)
    await _release(conn, offer, "declined", actor_type="rider", actor_id=user_id)


async def offer_undeliverable(conn: Conn, offer_id: UUID) -> None:
    """The offer SMS never arrived: treat the rider as unreachable for a while
    and move on (spec section 9)."""
    offer = await _lock_offer(conn, offer_id)
    if offer is None or offer["response"] is not None:
        return
    await _release(conn, offer, "undeliverable", actor_type="provider")
    await conn.execute(
        "update engine.riders set unreachable_until = now() + %s where id = %s",
        (UNREACHABLE_FOR, offer["rider_id"]),
    )
    await events.record(
        conn,
        "rider.unreachable",
        actor_type="system",
        pickup_id=offer["pickup_id"],
        payload={"rider_id": str(offer["rider_id"])},
    )


async def withdraw_open_offer(conn: Conn, pickup_id: UUID) -> None:
    """Close the pickup's open offer without re-dispatching (it was cancelled)."""
    await conn.execute(
        """
        update engine.dispatch_offers set response = 'withdrawn', responded_at = now()
         where pickup_id = %s and response is null
        """,
        (pickup_id,),
    )


@jobs.handler("offer.expire")
async def _expire_job(conn: Conn, payload: dict) -> None:
    offer = await _lock_offer(conn, UUID(payload["offer_id"]))
    if offer is None or offer["response"] is not None:
        return
    if not offer["is_expired"]:
        raise RuntimeError("offer.expire ran before the offer expired")
    await _release(conn, offer, "expired", actor_type="system")
    cur = await conn.execute(
        """
        update engine.riders set missed_offers = missed_offers + 1 where id = %s
        returning missed_offers, user_id
        """,
        (offer["rider_id"],),
    )
    rider = await cur.fetchone()
    if rider["missed_offers"] >= MAX_MISSED_OFFERS:
        await set_duty(conn, offer["rider_id"], on_duty=False, actor_type="system", user_id=None)
        await notify.queue_sms(
            conn,
            user_id=rider["user_id"],
            template="off_duty",
            text="Imfucuza: you missed 3 jobs in a row, so you are now off duty. "
            "Go back on duty in the app when you are ready.",
        )


async def set_duty(
    conn: Conn, rider_id: UUID, *, on_duty: bool, actor_type: str, user_id: UUID | None
) -> None:
    await conn.execute(
        """
        update engine.riders
           set on_duty = %s, missed_offers = 0,
               duty_since = case when %s then now() else null end
         where id = %s
        """,
        (on_duty, on_duty, rider_id),
    )
    await events.record(
        conn,
        "rider.duty",
        actor_type=actor_type,
        actor_id=user_id,
        payload={"rider_id": str(rider_id), "on_duty": on_duty},
    )


@dataclass(frozen=True)
class LocationFix:
    lat: float
    lng: float
    accuracy_m: int | None
    reported_at: datetime
    mock_location: bool = False


async def record_locations(conn: Conn, rider_id: UUID, fixes: list[LocationFix]) -> None:
    cur = await conn.execute(
        """
        select id from engine.pickup_requests
         where assigned_rider_id = %s and status = any(%s)
         order by assigned_at desc limit 1
        """,
        (rider_id, list(ACTIVE_JOB_STATUSES)),
    )
    job = await cur.fetchone()
    async with conn.cursor() as cursor:
        await cursor.executemany(
            """
            insert into engine.rider_locations
                (rider_id, source, lat, lng, accuracy_m, mock_location, pickup_id, reported_at)
            values (%s, 'gps', %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    rider_id,
                    f.lat,
                    f.lng,
                    f.accuracy_m,
                    f.mock_location,
                    job["id"] if job else None,
                    f.reported_at,
                )
                for f in fixes
            ],
        )
    if any(f.mock_location for f in fixes):
        await events.record(
            conn, "gps.anomaly", actor_type="system", payload={"rider_id": str(rider_id)}
        )


async def record_self_report(
    conn: Conn, rider_id: UUID, *, source: str, lat: float, lng: float
) -> None:
    """A location chosen from the zone or landmark menu, for riders without GPS.
    Capped and logged: a rider who keeps claiming a busy spot shows up."""
    cur = await conn.execute(
        """
        select r.channel, r.user_id,
               (select count(*) from engine.rider_locations l
                 where l.rider_id = r.id and l.source in ('landmark', 'zone')
                   and l.received_at > now() - interval '1 hour') as recent
          from engine.riders r where r.id = %s
        """,
        (rider_id,),
    )
    rider = await cur.fetchone()
    if rider["channel"] != "ussd":
        raise SelfReportNotAllowed("app riders share their location by GPS")
    if rider["recent"] >= SELF_REPORTS_PER_HOUR:
        raise TooManyLocationChanges("too many location changes in the last hour")
    await events.record(
        conn,
        "rider.location_self_reported",
        actor_type="rider",
        actor_id=rider["user_id"],
        payload={"rider_id": str(rider_id), "source": source},
    )
    await conn.execute(
        """
        insert into engine.rider_locations (rider_id, source, lat, lng, reported_at)
        values (%s, %s, %s, %s, now())
        """,
        (rider_id, source, lat, lng),
    )


async def _lock_offer(conn: Conn, offer_id: UUID) -> dict | None:
    # Expiry is judged by the database clock, the same clock that set it.
    cur = await conn.execute(
        """
        select *, expires_at <= now() as is_expired
          from engine.dispatch_offers where id = %s for update
        """,
        (offer_id,),
    )
    return await cur.fetchone()


async def _open_offer(conn: Conn, offer_id: UUID, rider_id: UUID) -> dict:
    offer = await _lock_offer(conn, offer_id)
    if offer is None or offer["rider_id"] != rider_id:
        raise OfferNotFound("offer not found")
    if offer["response"] is not None:
        raise OfferNotAvailable(f"offer was already {offer['response']}")
    return offer


async def _release(
    conn: Conn, offer: dict, response: str, *, actor_type: str, actor_id: UUID | None = None
) -> None:
    await conn.execute(
        "update engine.dispatch_offers set response = %s, responded_at = now() where id = %s",
        (response, offer["id"]),
    )
    cur = await conn.execute(
        "select status, version from engine.pickup_requests where id = %s", (offer["pickup_id"],)
    )
    pickup = await cur.fetchone()
    if pickup["status"] == Status.OFFERED:
        await pickups.apply(
            conn,
            offer["pickup_id"],
            Event.OFFER_RELEASED,
            expected_version=pickup["version"],
            actor_type=actor_type,
            actor_id=actor_id,
            payload={"offer_id": str(offer["id"]), "response": response},
        )
        await jobs.enqueue(conn, "dispatch.next", {"pickup_id": str(offer["pickup_id"])})


async def _pickup_version(conn: Conn, pickup_id: UUID) -> int:
    cur = await conn.execute(
        "select version from engine.pickup_requests where id = %s", (pickup_id,)
    )
    return (await cur.fetchone())["version"]


def _jsonable(c: Candidate) -> dict:
    data = asdict(c)
    data["rider_id"], data["user_id"] = str(c.rider_id), str(c.user_id)
    return data

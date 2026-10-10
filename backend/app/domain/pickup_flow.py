"""Steps of a pickup that a household or rider takes directly."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import jobs
from app.config import get_settings
from app.db import Conn
from app.domain import dispatch, events, notify, payments, pickups, review
from app.domain.pickups import Event, Offering, Status

# A rider's current jobs: busy ones, plus those waiting for the household's PIN.
ACTIVE_FOR_RIDER = (*dispatch.ACTIVE_JOB_STATUSES, Status.AWAITING_PIN)

# A self-reported arrival can't beat a tricycle at about 18 km/h, capped at 15 min.
RIDER_SPEED_M_S = 5
MAX_TRIP_S = 15 * 60
# Arrived but not collected after this long: ask a supervisor.
ARRIVAL_TIMEOUT = timedelta(minutes=45)


class NotYourPickup(LookupError):
    pass


class TooSoon(Exception):
    def __init__(self, seconds_left: int) -> None:
        super().__init__(f"you can't have arrived yet: try again in {seconds_left // 60 + 1} min")
        self.seconds_left = seconds_left


class TooFarAway(Exception):
    def __init__(self, distance_m: int) -> None:
        super().__init__(f"you are {distance_m} m from the pickup")
        self.distance_m = distance_m


async def create(
    conn: Conn,
    *,
    household_id: UUID,
    user_id: UUID,
    offering: Offering,
    lat: float | None,
    lng: float | None,
) -> UUID:
    if offering is not Offering.REFUSE:
        raise NotImplementedError("plastic collection is not available yet")
    if lat is None or lng is None:
        cur = await conn.execute(
            "select lat, lng from engine.households where id = %s", (household_id,)
        )
        home = await cur.fetchone()
        lat, lng = home["lat"], home["lng"]
    if lat is None or lng is None:
        raise ValueError("a pickup needs a location: send one or save it on your profile")

    settings = get_settings()
    cur = await conn.execute(
        """
        insert into engine.pickup_requests (household_id, offering, status, fee_pesewas, lat, lng)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            household_id,
            offering,
            pickups.initial_status(offering),
            settings.refuse_fee_pesewas,
            lat,
            lng,
        ),
    )
    pickup_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "pickup.requested",
        actor_type="household",
        actor_id=user_id,
        pickup_id=pickup_id,
        payload={"offering": offering},
    )
    await jobs.enqueue(
        conn,
        "payment.expire",
        {"pickup_id": str(pickup_id)},
        run_at=datetime.now(UTC) + timedelta(seconds=settings.payment_window_s),
        dedupe_key=f"payment-expire:{pickup_id}",
    )
    return pickup_id


async def cancel(conn: Conn, pickup_id: UUID, *, household_id: UUID, user_id: UUID) -> bool:
    """Cancel before the rider arrives. Returns whether a refund was started."""
    pickup = await _pickup(conn, pickup_id)
    if pickup["household_id"] != household_id:
        raise NotYourPickup("pickup not found")
    # Offer before pickup, the same lock order as accepting an offer.
    await dispatch.withdraw_open_offer(conn, pickup_id)
    await pickups.apply(
        conn,
        pickup_id,
        Event.CANCELLED_BY_HOUSEHOLD,
        expected_version=pickup["version"],
        actor_type="household",
        actor_id=user_id,
    )
    if pickup["rider_user_id"]:
        await notify.queue_sms(
            conn,
            user_id=pickup["rider_user_id"],
            pickup_id=pickup_id,
            template="cancelled",
            text="Imfucuza: the household cancelled your current pickup. No need to go.",
        )
    return await payments.start_refund(conn, pickup_id)


async def arrive(
    conn: Conn,
    pickup_id: UUID,
    *,
    rider_id: UUID,
    user_id: UUID,
    fix: dispatch.LocationFix,
) -> pickups.Transitioned:
    pickup = await assigned_to(conn, pickup_id, rider_id)
    await dispatch.record_locations(conn, rider_id, [fix])
    distance = round(dispatch.haversine_m(pickup["lat"], pickup["lng"], fix.lat, fix.lng))
    if distance > get_settings().arrival_radius_m + (fix.accuracy_m or 0):
        raise TooFarAway(distance)
    result = await pickups.apply(
        conn,
        pickup_id,
        Event.RIDER_ARRIVED,
        expected_version=pickup["version"],
        actor_type="rider",
        actor_id=user_id,
        changes={"arrived_at": pickups.NOW},
        payload={"distance_m": distance},
    )
    await _watch_arrival(conn, pickup_id, result.version)
    return result


async def arrive_self_reported(
    conn: Conn, pickup_id: UUID, *, rider_id: UUID, user_id: UUID
) -> pickups.Transitioned:
    """A feature-phone rider has no GPS, so arrival is their word. It can't come
    sooner than the trip could take, and the household is told so it can object."""
    pickup = await assigned_to(conn, pickup_id, rider_id)
    cur = await conn.execute(
        """
        select r.channel,
               greatest(0, least(%s, o.distance_m / %s)
                   - extract(epoch from now() - o.responded_at))::int as seconds_left
          from engine.riders r
          join engine.dispatch_offers o
            on o.rider_id = r.id and o.pickup_id = %s and o.response = 'accepted'
         where r.id = %s
         order by o.responded_at desc limit 1
        """,
        (MAX_TRIP_S, RIDER_SPEED_M_S, pickup_id, rider_id),
    )
    trip = await cur.fetchone()
    if trip is None or trip["channel"] != "ussd":
        raise dispatch.SelfReportNotAllowed("app riders mark arrival from the app, by GPS")
    if trip["seconds_left"] > 0:
        raise TooSoon(trip["seconds_left"])

    result = await pickups.apply(
        conn,
        pickup_id,
        Event.RIDER_ARRIVED,
        expected_version=pickup["version"],
        actor_type="rider",
        actor_id=user_id,
        changes={"arrived_at": pickups.NOW},
        payload={"self_reported": True},
    )
    # The rider is now where the pickup is: place them there for the next dispatch.
    await conn.execute(
        """
        insert into engine.rider_locations (rider_id, source, lat, lng, pickup_id, reported_at)
        values (%s, 'arrival', %s, %s, %s, now())
        """,
        (rider_id, pickup["lat"], pickup["lng"], pickup_id),
    )
    cur = await conn.execute(
        "select user_id from engine.households where id = %s", (pickup["household_id"],)
    )
    code = get_settings().ussd_code or "the Imfucuza code"
    await notify.queue_sms(
        conn,
        user_id=(await cur.fetchone())["user_id"],
        pickup_id=pickup_id,
        template="rider_arrived",
        text=f"Imfucuza: your rider says they have arrived. Not there? Dial {code}, choose 2.",
    )
    await _watch_arrival(conn, pickup_id, result.version)
    return result


async def _watch_arrival(conn: Conn, pickup_id: UUID, version: int) -> None:
    await jobs.enqueue(
        conn,
        "arrival.check",
        {"pickup_id": str(pickup_id), "version": version},
        run_at=datetime.now(UTC) + ARRIVAL_TIMEOUT,
        dedupe_key=f"arrival-check:{pickup_id}:{version}",
    )


@jobs.handler("arrival.check")
async def _arrival_check(conn: Conn, payload: dict) -> None:
    """Arrived but never collected: the household's money is held, so a person looks."""
    cur = await conn.execute(
        "select status, version from engine.pickup_requests where id = %s",
        (payload["pickup_id"],),
    )
    pickup = await cur.fetchone()
    if pickup["status"] == Status.ARRIVED and pickup["version"] == payload["version"]:
        await review.open_item(
            conn,
            "dispatch_stalled",
            pickup_id=UUID(payload["pickup_id"]),
            payload={"reason": "arrived but not collected", "pickup_version": pickup["version"]},
        )


async def collected(
    conn: Conn, pickup_id: UUID, *, rider_id: UUID, user_id: UUID
) -> pickups.Transitioned:
    pickup = await assigned_to(conn, pickup_id, rider_id)
    result = await pickups.apply(
        conn,
        pickup_id,
        Event.REFUSE_COLLECTED,
        expected_version=pickup["version"],
        actor_type="rider",
        actor_id=user_id,
        changes={"collected_at": pickups.NOW},
    )
    await jobs.enqueue(
        conn,
        "pin.issue",
        {"pickup_id": str(pickup_id)},
        dedupe_key=f"pin-issue:{pickup_id}:{result.version}",
        max_attempts=10,
    )
    # A fallback deadline in case no PIN is ever sent. Each PIN adds its own.
    await jobs.enqueue(
        conn,
        "pin.expire",
        {"pickup_id": str(pickup_id)},
        run_at=datetime.now(UTC) + timedelta(seconds=get_settings().pin_ttl_s),
        dedupe_key=f"pin-expire:{pickup_id}",
    )
    return result


async def _pickup(conn: Conn, pickup_id: UUID) -> dict:
    cur = await conn.execute(
        """
        select p.*, r.user_id as rider_user_id
          from engine.pickup_requests p
          left join engine.riders r on r.id = p.assigned_rider_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup is None:
        raise NotYourPickup("pickup not found")
    return pickup


async def assigned_to(conn: Conn, pickup_id: UUID, rider_id: UUID) -> dict:
    pickup = await _pickup(conn, pickup_id)
    if pickup["assigned_rider_id"] != rider_id or pickup["status"] in pickups.TERMINAL:
        raise NotYourPickup("pickup not found")
    return pickup

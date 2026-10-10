"""What supervisors do: work the review queue, step into pickups, manage riders
and their payouts, and keep zones and landmarks up to date.

Every action goes through the same state machine and ledger as everything
else, and is written to the event log with the supervisor who did it.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import errors

from app import jobs
from app.config import get_settings
from app.db import Conn
from app.domain import dispatch, events, ledger, money, notify, payments, pickups, pins, review
from app.domain.ledger import RIDER_PAYABLE, Account
from app.domain.pickups import Event, Status

# Another supervisor may take over a claim left this long.
STALE_CLAIM = timedelta(hours=1)
# These items are the only thing pointing at a stuck pickup, so they stay
# open until something has happened to the pickup.
OPEN_UNTIL_PICKUP_MOVES = frozenset({"dispatch_stalled"})
# A supervisor can send PINs past the household's own resend cap, up to this many.
MAX_PINS_WITH_SUPERVISOR = 2 * pins.MAX_PINS_PER_PICKUP
# A new payout number holds payouts this long (threat model T3).
PAYOUT_NUMBER_HOLD = timedelta(hours=48)
# Payouts to one rider within this period count together against the
# second-approval threshold, so splitting a payout doesn't avoid it.
PAYOUT_APPROVAL_PERIOD = timedelta(hours=24)


class ReviewItemUnavailable(Exception):
    """Resolved already, claimed by another supervisor, or not fixed yet."""


class PayoutNotAllowed(Exception):
    pass


class RiderUnavailable(Exception):
    pass


# Review queue ----------------------------------------------------------------


async def list_review_items(
    conn: Conn, *, status: str, type_: str | None, limit: int
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select r.id, r.type, r.status, r.pickup_id, r.created_at, r.claimed_at,
               r.payload->>'reason' as reason, u.name as assigned_to_name, r.assigned_to
          from engine.review_items r
          left join engine.users u on u.id = r.assigned_to
         where r.status = %s and (%s::text is null or r.type = %s)
         order by r.created_at
         limit %s
        """,
        (status, type_, type_, limit),
    )
    return await cur.fetchall()


async def review_item_detail(conn: Conn, item_id: UUID) -> dict[str, Any] | None:
    """Everything a supervisor needs to decide, in one place. PIN hashes are
    never included, and SMS bodies only as stored, with secrets masked."""
    cur = await conn.execute("select * from engine.review_items where id = %s", (item_id,))
    item = await cur.fetchone()
    if item is None:
        return None
    detail: dict[str, Any] = {"item": item}
    pickup_id = item["pickup_id"]
    if pickup_id is None:
        return detail

    queries = {
        "pickup": """
            select id, offering, status, fee_pesewas, requested_at, paid_at, assigned_at,
                   arrived_at, collected_at, completed_at, assigned_rider_id, household_id
              from engine.pickup_requests where id = %s""",
        "timeline": """
            select occurred_at, name, actor_type, payload
              from engine.events where pickup_id = %s order by id""",
        "ledger": """
            select t.kind, t.idempotency_key, t.created_at, a.code, e.side, e.amount
              from engine.ledger_transactions t
              join engine.ledger_entries e on e.txn_id = t.id
              join engine.ledger_accounts a on a.id = e.account_id
             where t.pickup_id = %s order by t.id, e.id""",
        "payments": """
            select direction, reference, status, amount_pesewas, fee_pesewas, created_at
              from engine.payments where pickup_id = %s order by created_at""",
        "pins": """
            select status, attempts, expires_at, confirmed_at, confirmed_via, created_at
              from engine.pins where pickup_id = %s order by created_at""",
        "offers": """
            select rider_id, distance_m, location_source, channel, sent_at, response,
                   responded_at
              from engine.dispatch_offers where pickup_id = %s order by sent_at""",
        "messages": """
            select template, body_masked, status, created_at
              from engine.notifications where pickup_id = %s order by created_at""",
    }
    for key, query in queries.items():
        cur = await conn.execute(query, (pickup_id,))
        detail[key] = await cur.fetchone() if key == "pickup" else await cur.fetchall()
    return detail


async def claim(conn: Conn, item_id: UUID, supervisor_id: UUID) -> None:
    cur = await conn.execute(
        """
        update engine.review_items set assigned_to = %(me)s, claimed_at = now()
         where id = %(id)s and status = 'open'
           and (assigned_to is null or assigned_to = %(me)s or claimed_at < now() - %(stale)s)
        returning id
        """,
        {"me": supervisor_id, "id": item_id, "stale": STALE_CLAIM},
    )
    if await cur.fetchone() is None:
        raise ReviewItemUnavailable("this item is resolved or claimed by someone else")
    await events.record(
        conn,
        "review.claimed",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"review_item_id": str(item_id)},
    )


async def resolve(conn: Conn, item_id: UUID, supervisor_id: UUID, resolution: str) -> None:
    cur = await conn.execute(
        """
        select r.type, (r.payload->>'pickup_version')::int as raised_at_version, p.version
          from engine.review_items r
          left join engine.pickup_requests p on p.id = r.pickup_id
         where r.id = %s
        """,
        (item_id,),
    )
    found = await cur.fetchone()
    if (
        found
        and found["type"] in OPEN_UNTIL_PICKUP_MOVES
        and found["raised_at_version"] == found["version"]
    ):
        raise ReviewItemUnavailable(
            "the pickup hasn't moved since this was raised: sort it out first"
        )
    cur = await conn.execute(
        """
        update engine.review_items
           set status = 'resolved', resolved_at = now(), resolved_by = %(me)s,
               resolution = %(resolution)s, assigned_to = %(me)s,
               claimed_at = coalesce(claimed_at, now())
         where id = %(id)s and status = 'open'
           and (assigned_to is null or assigned_to = %(me)s or claimed_at < now() - %(stale)s)
        returning pickup_id, type
        """,
        {"me": supervisor_id, "resolution": resolution, "id": item_id, "stale": STALE_CLAIM},
    )
    item = await cur.fetchone()
    if item is None:
        raise ReviewItemUnavailable("this item is resolved or claimed by someone else")
    await events.record(
        conn,
        "review.resolved",
        actor_type="supervisor",
        actor_id=supervisor_id,
        pickup_id=item["pickup_id"],
        payload={"review_item_id": str(item_id), "type": item["type"]},
    )


# Stepping into a pickup ----------------------------------------------------------


async def reissue_pin(conn: Conn, pickup_id: UUID, supervisor_id: UUID) -> None:
    """The only way to clear a locked PIN. The household gets a new one by SMS."""
    pickup = await _pickup(conn, pickup_id)
    cur = await conn.execute(
        """
        select (select count(*) from engine.pins where pickup_id = %(id)s) as issued,
               exists (select 1 from engine.jobs
                        where kind = 'pin.issue' and payload->>'pickup_id' = %(id)s::text
                          and done_at is null and failed_at is null) as on_its_way
        """,
        {"id": pickup_id},
    )
    sent = await cur.fetchone()
    if sent["issued"] >= MAX_PINS_WITH_SUPERVISOR:
        raise pins.TooManyAttempts("no more PINs can be sent for this pickup: cancel it instead")
    if pickup["status"] == Status.DISPUTED:
        await pickups.apply(
            conn,
            pickup_id,
            Event.DISPUTE_PIN_REISSUED,
            expected_version=pickup["version"],
            actor_type="supervisor",
            actor_id=supervisor_id,
        )
        # The earlier PINs' timers have already passed over this pickup, so the
        # new round needs a deadline even if its PIN is never sent.
        await jobs.enqueue(
            conn,
            "pin.expire",
            {"pickup_id": str(pickup_id)},
            run_at=datetime.now(UTC) + timedelta(seconds=get_settings().pin_ttl_s),
        )
    elif pickup["status"] != Status.AWAITING_PIN:
        raise pickups.InvalidTransition(f"pickup is {pickup['status']}, not waiting for a PIN")
    if not sent["on_its_way"]:
        await jobs.enqueue(conn, "pin.issue", {"pickup_id": str(pickup_id)}, max_attempts=10)
    await events.record(
        conn,
        "supervisor.pin_reissued",
        actor_type="supervisor",
        actor_id=supervisor_id,
        pickup_id=pickup_id,
    )


async def cancel(conn: Conn, pickup_id: UUID, supervisor_id: UUID, reason: str) -> bool:
    """Cancel and refund in full. Returns whether a refund was started."""
    pickup = await _pickup(conn, pickup_id)
    # Lock the PINs before the pickup, as a confirm does, then expire them in a
    # new statement, which also sees a PIN a resend has just committed.
    await conn.execute(
        "select id from engine.pins where pickup_id = %s and status in ('active', 'locked')"
        " for update",
        (pickup_id,),
    )
    await conn.execute(
        "update engine.pins set status = 'expired' where pickup_id = %s"
        " and status in ('active', 'locked')",
        (pickup_id,),
    )
    await dispatch.withdraw_open_offer(conn, pickup_id)
    await pickups.apply(
        conn,
        pickup_id,
        Event.CANCELLED_BY_SUPERVISOR,
        expected_version=pickup["version"],
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"reason": reason},
    )
    refunding = await payments.start_refund(conn, pickup_id)
    await notify.queue_sms(
        conn,
        user_id=pickup["household_user_id"],
        pickup_id=pickup_id,
        template="supervisor_cancelled",
        text="Imfucuza: a supervisor cancelled your pickup."
        + (" Your payment is being refunded." if refunding else ""),
    )
    if pickup["rider_user_id"]:
        await notify.queue_sms(
            conn,
            user_id=pickup["rider_user_id"],
            pickup_id=pickup_id,
            template="supervisor_cancelled",
            text="Imfucuza: a supervisor cancelled your current pickup.",
        )
    return refunding


async def redispatch(conn: Conn, pickup_id: UUID, supervisor_id: UUID) -> None:
    """Take a job back from a rider who isn't coming, and offer it again."""
    pickup = await _pickup(conn, pickup_id)
    if pickup["window_closing"]:
        # Too late for another rider to get an offer: dispatch would end the
        # window and refund.
        raise pickups.InvalidTransition("too late to look for another rider: assign one, or cancel")
    await pickups.apply(
        conn,
        pickup_id,
        Event.SUPERVISOR_REDISPATCHED,
        expected_version=pickup["version"],
        actor_type="supervisor",
        actor_id=supervisor_id,
        changes={"assigned_rider_id": None, "assigned_at": None},
        payload={"from_rider_id": str(pickup["assigned_rider_id"])},
    )
    await jobs.enqueue(conn, "dispatch.next", {"pickup_id": str(pickup_id)})
    await _tell_rider_job_moved(conn, pickup)


async def assign(conn: Conn, pickup_id: UUID, rider_id: UUID, supervisor_id: UUID) -> None:
    """Hand a waiting pickup, or one whose rider isn't coming, to a chosen rider.
    Recorded as an accepted offer so the dispatch history stays complete."""
    pickup = await _pickup(conn, pickup_id)
    if pickup["assigned_rider_id"] == rider_id:
        raise RiderUnavailable("this rider already has this pickup")
    # Lock the rider so two assignments can't both find them free.
    cur = await conn.execute(
        """
        select r.user_id, r.channel, r.max_active_jobs, u.status
          from engine.riders r join engine.users u on u.id = r.user_id
         where r.id = %s
           for no key update of r
        """,
        (rider_id,),
    )
    rider = await cur.fetchone()
    if rider is None or rider["status"] != "active":
        raise RiderUnavailable("rider not found or not active")
    cur = await conn.execute(
        """
        select (select count(*) from engine.pickup_requests
                 where assigned_rider_id = %(rider)s and status = any(%(active)s)) as busy,
               exists (select 1 from engine.dispatch_offers
                        where rider_id = %(rider)s and response is null) as offered
        """,
        {"rider": rider_id, "active": list(dispatch.ACTIVE_JOB_STATUSES)},
    )
    load = await cur.fetchone()
    if load["busy"] >= rider["max_active_jobs"]:
        raise RiderUnavailable("rider is busy with another pickup")
    if load["offered"]:
        raise RiderUnavailable("rider has a job offer open: wait for their answer")
    await conn.execute(
        """
        insert into engine.dispatch_offers
            (pickup_id, rider_id, rank, distance_m, location_source, channel,
             expires_at, responded_at, response)
        values (%s, %s, 0, null, 'supervisor', %s, now(), now(), 'accepted')
        """,
        (pickup_id, rider_id, rider["channel"]),
    )
    await pickups.apply(
        conn,
        pickup_id,
        Event.SUPERVISOR_ASSIGNED,
        expected_version=pickup["version"],
        actor_type="supervisor",
        actor_id=supervisor_id,
        changes={"assigned_rider_id": rider_id, "assigned_at": pickups.NOW},
        payload={"rider_id": str(rider_id), "from_rider_id": _str(pickup["assigned_rider_id"])},
    )
    if rider["channel"] == "ussd":
        await jobs.enqueue(
            conn,
            "sms.job_details",
            {"pickup_id": str(pickup_id), "rider_user_id": str(rider["user_id"])},
        )
    else:
        await notify.queue_sms(
            conn,
            user_id=rider["user_id"],
            pickup_id=pickup_id,
            template="supervisor_assigned",
            text="Imfucuza: a supervisor gave you a pickup. Open the app for the details.",
        )
    await _tell_rider_job_moved(conn, pickup)


async def _tell_rider_job_moved(conn: Conn, pickup: dict[str, Any]) -> None:
    if pickup["rider_user_id"]:
        await notify.queue_sms(
            conn,
            user_id=pickup["rider_user_id"],
            pickup_id=pickup["id"],
            template="redispatched",
            text="Imfucuza: your current pickup was given to another rider.",
        )


# Riders ----------------------------------------------------------------------


async def list_riders(conn: Conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select r.id, u.name, u.phone_e164, u.status, r.channel, r.on_duty,
               r.unreachable_until, r.missed_offers, r.payout_msisdn,
               r.payout_msisdn_changed_at, coalesce(a.balance, 0) as owed_pesewas,
               (select max(received_at) from engine.rider_locations l where l.rider_id = r.id)
                 as last_location_at
          from engine.riders r
          join engine.users u on u.id = r.user_id
          left join engine.ledger_accounts a on a.code = 'rider_payable:' || r.id
         order by u.name nulls last
        """
    )
    return await cur.fetchall()


async def set_rider_status(
    conn: Conn, rider_id: UUID, *, active: bool, supervisor_id: UUID
) -> None:
    cur = await conn.execute(
        """
        update engine.users set status = %s
         where id = (select user_id from engine.riders where id = %s)
           and status in ('active', 'suspended')
        returning id
        """,
        ("active" if active else "suspended", rider_id),
    )
    if await cur.fetchone() is None:
        raise RiderUnavailable("rider not found")
    if not active:
        # The offer before the rider row, the order accepting an offer takes them in.
        await dispatch.withdraw_rider_offer(conn, rider_id)
        await dispatch.set_duty(
            conn, rider_id, on_duty=False, actor_type="supervisor", user_id=None
        )
        cur = await conn.execute(
            """
            select id, version from engine.pickup_requests
             where assigned_rider_id = %s and status = any(%s)
            """,
            (rider_id, list(dispatch.ACTIVE_JOB_STATUSES)),
        )
        for job in await cur.fetchall():
            await review.open_item(
                conn,
                "dispatch_stalled",
                pickup_id=job["id"],
                rider_id=rider_id,
                payload={"reason": "rider suspended mid-job", "pickup_version": job["version"]},
            )
    await events.record(
        conn,
        "supervisor.rider_status",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"rider_id": str(rider_id), "active": active},
    )


async def set_payout_number(
    conn: Conn, rider_id: UUID, phone_e164: str, *, supervisor_id: UUID
) -> None:
    cur = await conn.execute(
        "select user_id, payout_msisdn from engine.riders where id = %s for no key update",
        (rider_id,),
    )
    rider = await cur.fetchone()
    if rider is None:
        raise RiderUnavailable("rider not found")
    if rider["payout_msisdn"] == phone_e164:
        return
    cur = await conn.execute(
        "select 1 from engine.payouts where rider_id = %s"
        " and status in ('pending_approval', 'approved')",
        (rider_id,),
    )
    if await cur.fetchone():
        # Money for one of these may be on its way to the old number.
        raise PayoutNotAllowed("record or reject this rider's open payouts first")
    await conn.execute(
        """
        update engine.riders set payout_msisdn = %s, payout_msisdn_changed_at = now()
         where id = %s
        """,
        (phone_e164, rider_id),
    )
    await notify.queue_sms(
        conn,
        user_id=rider["user_id"],
        template="payout_number_changed",
        text=f"Imfucuza: your payout number is now the one ending {phone_e164[-3:]}. "
        "Payouts are held for 48 hours. Not you? Tell your supervisor.",
    )
    await events.record(
        conn,
        "supervisor.payout_number_changed",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"rider_id": str(rider_id)},
    )


# Payouts ---------------------------------------------------------------------
#
# A payout is requested, approved by a second supervisor if it's large, sent by
# hand over mobile money, and recorded with the MoMo reference. Only recording
# touches the ledger. Requested and approved payouts reserve the rider's balance.


@dataclass(frozen=True)
class PayoutResult:
    payout_id: UUID
    status: str


def clean_reference(reference: str) -> str:
    """One spelling per MoMo transaction, so case or spacing can't record it twice."""
    cleaned = "".join(reference.split()).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{3,39}", cleaned):
        raise ValueError("use the 4 to 40 letters and digits of the mobile money reference")
    return cleaned


async def payable_now(conn: Conn, rider_id: UUID) -> int:
    return max(await _available(conn, rider_id), 0)


async def _available(conn: Conn, rider_id: UUID) -> int:
    """Earnings minus anything still in its dispute window, minus payouts not
    yet recorded. One statement, so every part comes from the same snapshot."""
    cur = await conn.execute(
        """
        select coalesce((select balance from engine.ledger_accounts where code = %(code)s), 0)
             - coalesce((select sum(e.amount) from engine.ledger_entries e
                          join engine.ledger_accounts a on a.id = e.account_id
                          join engine.ledger_transactions t on t.id = e.txn_id
                         where a.code = %(code)s and e.side = 'credit' and t.kind = 'release'
                           and e.created_at > now() - make_interval(secs => %(hold)s)), 0)
             - coalesce((select sum(amount_pesewas) from engine.payouts
                          where rider_id = %(rider)s
                            and status in ('pending_approval', 'approved')), 0)
          as available
        """,
        {
            "code": Account(RIDER_PAYABLE, rider_id).code,
            "hold": get_settings().payout_hold_s,
            "rider": rider_id,
        },
    )
    return int((await cur.fetchone())["available"])


async def _payout_destination(conn: Conn, rider_id: UUID) -> str:
    # Locking the rider serialises every payout change for them, so two
    # supervisors can't both pay out the same balance.
    cur = await conn.execute(
        """
        select payout_msisdn, payout_msisdn_changed_at > now() - %s as recently_changed
          from engine.riders where id = %s
           for no key update
        """,
        (PAYOUT_NUMBER_HOLD, rider_id),
    )
    rider = await cur.fetchone()
    if rider is None:
        raise RiderUnavailable("rider not found")
    if rider["payout_msisdn"] is None:
        raise PayoutNotAllowed("this rider has no payout number")
    if rider["recently_changed"]:
        raise PayoutNotAllowed("the payout number changed in the last 48 hours")
    return rider["payout_msisdn"]


async def request_payout(
    conn: Conn,
    rider_id: UUID,
    *,
    amount: int,
    supervisor_id: UUID,
    momo_reference: str | None = None,
) -> PayoutResult:
    """Small payouts are approved at once, and recorded too if the money has
    already been sent. Larger ones wait for a second supervisor."""
    destination = await _payout_destination(conn, rider_id)
    available = await _available(conn, rider_id)
    if amount > available:
        raise PayoutNotAllowed(f"only GHS {max(available, 0) / 100:.2f} can be paid out now")
    cur = await conn.execute(
        """
        select coalesce(sum(amount_pesewas), 0)::bigint as recent from engine.payouts
         where rider_id = %s and status <> 'rejected' and created_at > now() - %s
        """,
        (rider_id, PAYOUT_APPROVAL_PERIOD),
    )
    threshold = get_settings().payout_second_approval_pesewas
    needs_second = amount + (await cur.fetchone())["recent"] > threshold
    if needs_second and momo_reference:
        raise PayoutNotAllowed(
            f"over GHS {threshold / 100:.2f} a day needs a second supervisor before the money"
            " is sent: request it without a reference"
        )
    cur = await conn.execute(
        """
        insert into engine.payouts
            (rider_id, amount_pesewas, destination_msisdn, status, requested_by)
        values (%s, %s, %s, %s, %s)
        returning id, status
        """,
        (
            rider_id,
            amount,
            destination,
            "pending_approval" if needs_second else "approved",
            supervisor_id,
        ),
    )
    payout = await cur.fetchone()
    await events.record(
        conn,
        "supervisor.payout_requested",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"payout_id": str(payout["id"]), "rider_id": str(rider_id), "amount": amount},
    )
    if momo_reference:
        await record_payout(
            conn, payout["id"], momo_reference=momo_reference, supervisor_id=supervisor_id
        )
        return PayoutResult(payout["id"], "recorded")
    return PayoutResult(payout["id"], payout["status"])


async def list_payouts(conn: Conn, *, status: str, limit: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select p.id, p.rider_id, ru.name as rider_name, p.amount_pesewas, p.destination_msisdn,
               p.status, p.momo_reference, p.created_at, p.decided_at, p.recorded_at,
               req.name as requested_by_name, p.requested_by, p.approved_by
          from engine.payouts p
          join engine.riders r on r.id = p.rider_id
          join engine.users ru on ru.id = r.user_id
          join engine.users req on req.id = p.requested_by
         where p.status = %s
         order by p.created_at
         limit %s
        """,
        (status, limit),
    )
    return await cur.fetchall()


async def approve_payout(conn: Conn, payout_id: UUID, supervisor_id: UUID) -> None:
    payout = await _lock_payout(conn, payout_id)
    if payout["status"] != "pending_approval":
        raise PayoutNotAllowed("this payout is not waiting for approval")
    if payout["requested_by"] == supervisor_id:
        raise PayoutNotAllowed("a different supervisor must approve this payout")
    await conn.execute(
        """
        update engine.payouts set status = 'approved', approved_by = %s, decided_at = now()
         where id = %s
        """,
        (supervisor_id, payout_id),
    )
    await _payout_event(conn, "supervisor.payout_approved", payout_id, supervisor_id)


async def reject_payout(conn: Conn, payout_id: UUID, supervisor_id: UUID, reason: str) -> None:
    payout = await _lock_payout(conn, payout_id)
    if payout["status"] not in ("pending_approval", "approved"):
        raise PayoutNotAllowed(f"this payout is already {payout['status']}")
    # Rejecting frees the balance, so if the money was sent it could be paid twice.
    if payout["status"] == "approved" and payout["requested_by"] == supervisor_id:
        raise PayoutNotAllowed("a different supervisor must reject an approved payout")
    await conn.execute(
        """
        update engine.payouts
           set status = 'rejected', rejected_by = %s, rejection_reason = %s, decided_at = now()
         where id = %s
        """,
        (supervisor_id, reason, payout_id),
    )
    await _payout_event(conn, "supervisor.payout_rejected", payout_id, supervisor_id)


async def record_payout(
    conn: Conn, payout_id: UUID, *, momo_reference: str, supervisor_id: UUID
) -> None:
    """The money has been sent: record it against the rider's balance."""
    payout = await _lock_payout(conn, payout_id)
    if payout["status"] != "approved":
        raise PayoutNotAllowed(f"this payout is {payout['status']}, not approved")
    if await _available(conn, payout["rider_id"]) < 0:
        raise PayoutNotAllowed("the rider is now owed less than this payout")
    try:
        async with conn.transaction():
            await conn.execute(
                """
                update engine.payouts
                   set status = 'recorded', momo_reference = %s, recorded_by = %s,
                       recorded_at = now()
                 where id = %s
                """,
                (momo_reference, supervisor_id, payout_id),
            )
    except errors.UniqueViolation as exc:
        raise PayoutNotAllowed("this mobile money reference is already recorded") from exc
    result = await ledger.post(
        conn, money.rider_payout(payout["rider_id"], momo_reference, payout["amount_pesewas"])
    )
    await conn.execute(
        "update engine.payouts set ledger_txn_id = %s where id = %s", (result.txn_id, payout_id)
    )
    await _payout_event(conn, "supervisor.payout_recorded", payout_id, supervisor_id)


async def _lock_payout(conn: Conn, payout_id: UUID) -> dict[str, Any]:
    cur = await conn.execute("select rider_id from engine.payouts where id = %s", (payout_id,))
    found = await cur.fetchone()
    if found is None:
        raise LookupError("payout not found")
    # The rider before the payout, the order a new request takes them in.
    await conn.execute(
        "select 1 from engine.riders where id = %s for no key update", (found["rider_id"],)
    )
    cur = await conn.execute("select * from engine.payouts where id = %s for update", (payout_id,))
    return await cur.fetchone()


async def _payout_event(conn: Conn, name: str, payout_id: UUID, supervisor_id: UUID) -> None:
    await events.record(
        conn,
        name,
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"payout_id": str(payout_id)},
    )


# Zones and landmarks -----------------------------------------------------------


async def list_zones(conn: Conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select z.id, z.name, z.lat, z.lng, z.radius_m, z.ussd_index,
               coalesce(json_agg(json_build_object(
                   'id', l.id, 'name', l.name, 'lat', l.lat, 'lng', l.lng,
                   'ussd_index', l.ussd_index) order by l.ussd_index)
                 filter (where l.id is not null), '[]') as landmarks
          from engine.zones z
          left join engine.landmarks l on l.zone_id = z.id
         group by z.id
         order by z.ussd_index
        """
    )
    return await cur.fetchall()


async def create_zone(
    conn: Conn, *, name: str, lat: float, lng: float, radius_m: int, supervisor_id: UUID
) -> int:
    # One at a time, so two new zones never take the same menu number.
    await conn.execute("lock table engine.zones in share row exclusive mode")
    cur = await conn.execute(
        """
        insert into engine.zones (name, lat, lng, radius_m, ussd_index)
        select %s, %s, %s, %s, coalesce(max(ussd_index), 0) + 1 from engine.zones
        returning id
        """,
        (name, lat, lng, radius_m),
    )
    zone_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "supervisor.zone_created",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"zone_id": zone_id},
    )
    return zone_id


async def add_landmark(
    conn: Conn, zone_id: int, *, name: str, lat: float, lng: float, supervisor_id: UUID
) -> int:
    # Locking the zone keeps its landmarks' menu numbers from clashing.
    cur = await conn.execute(
        "select id from engine.zones where id = %s for no key update", (zone_id,)
    )
    if await cur.fetchone() is None:
        raise LookupError("zone not found")
    cur = await conn.execute(
        """
        insert into engine.landmarks (zone_id, name, lat, lng, ussd_index)
        select %s, %s, %s, %s, coalesce(max(ussd_index), 0) + 1
          from engine.landmarks where zone_id = %s
        returning id
        """,
        (zone_id, name, lat, lng, zone_id),
    )
    landmark_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "supervisor.landmark_created",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"zone_id": zone_id, "landmark_id": landmark_id},
    )
    return landmark_id


async def _pickup(conn: Conn, pickup_id: UUID) -> dict[str, Any]:
    cur = await conn.execute(
        """
        select p.id, p.status, p.version, p.assigned_rider_id,
               h.user_id as household_user_id, r.user_id as rider_user_id,
               coalesce(p.paid_at, p.requested_at)
                 < now() - make_interval(secs => %s) as window_closing
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
          left join engine.riders r on r.id = p.assigned_rider_id
         where p.id = %s
        """,
        (get_settings().dispatch_window_s - get_settings().offer_ttl_ussd_s, pickup_id),
    )
    pickup = await cur.fetchone()
    if pickup is None:
        raise LookupError("pickup not found")
    return pickup


def _str(value: Any) -> str | None:
    return None if value is None else str(value)

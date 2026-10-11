"""Complaints and disputes (engine-design.md sections 1 and 6).

Before giving its PIN a household can dispute a pickup: the money stays held
until a supervisor reissues the PIN or refunds, and an undecided dispute is
raised again after 48 hours rather than settled automatically. After the
pickup a complaint only opens a review item. Outcomes that cost the platform,
a refund after completion or points granted back after a wrongful rejection,
need a second supervisor past one supervisor's daily total (threat model T15)."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import errors

from app import jobs
from app.config import get_settings
from app.db import Conn
from app.domain import (
    events,
    ledger,
    money,
    notify,
    payments,
    pickup_flow,
    pickups,
    review,
    supervision,
)
from app.domain.pickups import Event, Offering, Status

# No rider has been involved yet: the household can simply cancel.
_TOO_EARLY = (Status.AWAITING_PAYMENT, Status.PENDING_DISPATCH, Status.OFFERED)


class ComplaintNotAllowed(Exception):
    pass


class OutcomeNotAllowed(Exception):
    pass


class Outcome(StrEnum):
    REISSUE_PIN = "reissue_pin"
    REFUND = "refund"
    GRANT_POINTS = "grant_points"
    DISMISS = "dismiss"


# A household complains -------------------------------------------------------------


async def open_complaint(
    conn: Conn, pickup_id: UUID, *, household_id: UUID, user_id: UUID, details: str, channel: str
) -> bool:
    """Returns whether the pickup is now disputed, with its money held."""
    settings = get_settings()
    # PINs before the pickup, the order a confirm takes them in.
    await conn.execute(
        "select id from engine.pins where pickup_id = %s and status in ('active', 'locked')"
        " for update",
        (pickup_id,),
    )
    pickup = await _pickup(conn, pickup_id)
    if pickup["household_id"] != household_id:
        raise pickup_flow.NotYourPickup("pickup not found")
    if pickup["status"] in _TOO_EARLY:
        raise ComplaintNotAllowed("no rider has come yet: cancel the pickup in the app instead")
    if pickup["status"] == Status.DISPUTED or await _open_items(conn, pickup_id):
        raise ComplaintNotAllowed("a supervisor is already looking into this pickup")
    disputed = pickup["status"] == Status.AWAITING_PIN
    if not disputed and pickup["closed_at"] < datetime.now(UTC) - timedelta(
        days=settings.complaint_window_days
    ):
        raise ComplaintNotAllowed(
            f"complaints close {settings.complaint_window_days} days after the pickup"
        )

    if disputed:
        # A rider who has the PIN can't confirm while the dispute is open. A new
        # statement, so it also catches a PIN a resend has just committed.
        await conn.execute(
            "update engine.pins set status = 'expired'"
            " where pickup_id = %s and status in ('active', 'locked')",
            (pickup_id,),
        )
        result = await pickups.apply(
            conn,
            pickup_id,
            Event.PROBLEM_REPORTED,
            expected_version=pickup["version"],
            actor_type="household",
            actor_id=user_id,
        )
        await jobs.enqueue(
            conn,
            "complaint.escalate",
            {"pickup_id": str(pickup_id), "version": result.version},
            run_at=datetime.now(UTC) + timedelta(seconds=settings.dispute_escalate_s),
            dedupe_key=f"complaint-escalate:{pickup_id}:{result.version}",
        )
        await _tell(
            conn,
            pickup["rider_user_id"],
            pickup_id,
            "Imfucuza: the household reported a problem with your current pickup. Payment is "
            "on hold until a supervisor decides.",
        )
    await review.open_item(
        conn,
        "household_complaint",
        pickup_id=pickup_id,
        household_id=household_id,
        payload={
            "reason": "disputed before the PIN" if disputed else "complaint after the pickup",
            "details": details,
            "channel": channel,
        },
    )
    await events.record(
        conn,
        "dispute.opened",
        actor_type="household",
        actor_id=user_id,
        pickup_id=pickup_id,
        payload={"disputed": disputed, "channel": channel},
    )
    return disputed


@jobs.handler("complaint.escalate")
async def _escalate(conn: Conn, payload: dict[str, Any]) -> None:
    pickup = await _pickup(conn, UUID(payload["pickup_id"]))
    if pickup["status"] == Status.DISPUTED and pickup["version"] == payload["version"]:
        await review.open_item(
            conn,
            "household_complaint",
            pickup_id=pickup["id"],
            household_id=pickup["household_id"],
            payload={"reason": "dispute undecided for 48 hours: the money is still held"},
        )


# A supervisor decides ----------------------------------------------------------------


async def decide(
    conn: Conn, pickup_id: UUID, *, supervisor_id: UUID, outcome: Outcome, note: str
) -> dict[str, Any]:
    pickup = await _pickup(conn, pickup_id)
    # A dispute is a complaint in itself, so it can be decided even if its
    # review item was closed some other way.
    await _complaint_for(
        conn, pickup_id, supervisor_id, required=pickup["status"] != Status.DISPUTED
    )

    if pickup["status"] == Status.DISPUTED:
        if outcome is Outcome.REISSUE_PIN:
            await supervision.reissue_pin(conn, pickup_id, supervisor_id, settles_dispute=True)
            await _tell(
                conn,
                pickup["rider_user_id"],
                pickup_id,
                "Imfucuza: a supervisor sent the household a new PIN for your pickup. Ask them "
                "for it to finish the job.",
            )
            return await _done(conn, pickup, supervisor_id, outcome, note)
        if outcome is Outcome.REFUND:
            await pickups.apply(
                conn,
                pickup_id,
                Event.DISPUTE_REFUNDED,
                expected_version=pickup["version"],
                actor_type="supervisor",
                actor_id=supervisor_id,
                payload={"note": note},
            )
            refunded = await payments.start_refund(conn, pickup_id)
            await _tell(
                conn,
                pickup["household_user_id"],
                pickup_id,
                "Imfucuza: a supervisor upheld your complaint."
                + (" Your payment is being refunded." if refunded else ""),
            )
            await _tell(
                conn,
                pickup["rider_user_id"],
                pickup_id,
                "Imfucuza: after the household's complaint, a supervisor closed your pickup "
                "without payment.",
            )
            return await _done(conn, pickup, supervisor_id, outcome, note)
        raise OutcomeNotAllowed("a disputed pickup needs its PIN reissued or a refund")

    if outcome is Outcome.DISMISS:
        if await _pending_decision(conn, pickup_id):
            raise OutcomeNotAllowed("a decision is waiting for approval: approve or reject it")
        await _tell(
            conn,
            pickup["household_user_id"],
            pickup_id,
            "Imfucuza: a supervisor looked into your complaint and found nothing to change.",
        )
        return await _done(conn, pickup, supervisor_id, outcome, note)
    if (
        outcome is Outcome.REFUND
        and pickup["offering"] == Offering.REFUSE
        and pickup["status"] == Status.COMPLETED
    ):
        refundable = await payments.unrefunded_collections(conn, pickup_id)
        amount = sum(c["amount_pesewas"] for c in refundable)
        if not amount:
            raise OutcomeNotAllowed("nothing paid for this pickup is left to refund")
        return await _costly(conn, pickup, "late_refund", amount, supervisor_id, note)
    if (
        outcome is Outcome.GRANT_POINTS
        and pickup["offering"] == Offering.PLASTIC
        # Rejected at the door, or collected and then turned down by a
        # supervisor, which voided its pending points.
        and (
            pickup["status"] == Status.REJECTED
            or (
                pickup["status"] == Status.COMPLETED
                and pickup["verification_outcome"] == "rejected"
            )
        )
    ):
        if not pickup["points_due"]:
            raise OutcomeNotAllowed("this pickup has no points to grant")
        return await _costly(
            conn, pickup, "points_grant", pickup["points_due"], supervisor_id, note
        )
    raise OutcomeNotAllowed(f"can't {outcome.replace('_', ' ')} a {pickup['status']} pickup")


async def _costly(
    conn: Conn, pickup: dict, kind: str, amount: int, supervisor_id: UUID, note: str
) -> dict[str, Any]:
    """Applied at once within the supervisor's daily total, otherwise held for
    another supervisor to approve."""
    settings = get_settings()
    limit = (
        settings.complaint_refund_daily_pesewas
        if kind == "late_refund"
        else settings.complaint_points_daily
    )
    # One supervisor's decisions in turn, so two at once can't both fit the total.
    await conn.execute(
        "select id from engine.users where id = %s for no key update", (supervisor_id,)
    )
    cur = await conn.execute(
        """
        select coalesce(sum(amount), 0)::int as total from engine.complaint_decisions
         where decided_by = %s and outcome = %s and status <> 'rejected'
           and created_at > now() - interval '1 day'
        """,
        (supervisor_id, kind),
    )
    over = (await cur.fetchone())["total"] + amount > limit
    try:
        async with conn.transaction():
            cur = await conn.execute(
                """
                insert into engine.complaint_decisions
                    (pickup_id, outcome, amount, note, status, decided_by)
                values (%s, %s, %s, %s, %s, %s)
                returning *
                """,
                (
                    pickup["id"],
                    kind,
                    amount,
                    note,
                    "pending_approval" if over else "applied",
                    supervisor_id,
                ),
            )
    except errors.UniqueViolation as exc:
        raise OutcomeNotAllowed("this has already been decided for the pickup") from exc
    decision = await cur.fetchone()
    if over:
        await events.record(
            conn,
            "complaint.awaiting_approval",
            actor_type="supervisor",
            actor_id=supervisor_id,
            pickup_id=pickup["id"],
            payload={"decision_id": str(decision["id"]), "outcome": kind, "amount": amount},
        )
        return {"outcome": kind, "status": "pending_approval", "decision_id": decision["id"]}
    await _apply(conn, decision, pickup)
    return {"outcome": kind, "status": "applied", "decision_id": decision["id"]}


async def _apply(conn: Conn, decision: dict, pickup: dict) -> None:
    if decision["outcome"] == "late_refund":
        refundable = await payments.unrefunded_collections(conn, pickup["id"])
        if sum(c["amount_pesewas"] for c in refundable) != decision["amount"]:
            raise OutcomeNotAllowed("what's left to refund has changed: decide again")
        await ledger.post(
            conn, money.dispute_refund(pickup["id"], pickup["household_id"], decision["amount"])
        )
        await payments.queue_refunds(conn, [c["reference"] for c in refundable])
        text = (
            "Imfucuza: a supervisor upheld your complaint. "
            f"Your GHS {decision['amount'] / 100:.2f} is being refunded."
        )
    else:
        await ledger.post(
            conn,
            money.points_retro_grant(pickup["id"], pickup["household_id"], decision["amount"]),
        )
        text = (
            "Imfucuza: a supervisor upheld your complaint. "
            f"{decision['amount']} points have been added to your balance."
        )
    await conn.execute(
        """
        update engine.complaint_decisions set status = 'applied', settled_at = now()
         where id = %s
        """,
        (decision["id"],),
    )
    # Reversals and grants per supervisor feed the S9 signal.
    await events.record(
        conn,
        "ledger.reversal",
        actor_type="supervisor",
        actor_id=decision["decided_by"],
        pickup_id=pickup["id"],
        payload={
            "decision_id": str(decision["id"]),
            "outcome": decision["outcome"],
            "amount": decision["amount"],
            "approved_by": str(decision["approved_by"]) if decision["approved_by"] else None,
        },
    )
    await _tell(conn, pickup["household_user_id"], pickup["id"], text)
    await _resolve_items(conn, pickup["id"], decision["decided_by"], decision["note"])


async def list_decisions(conn: Conn, *, status: str, limit: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select d.id, d.pickup_id, d.outcome, d.amount, d.note, d.status, d.created_at,
               d.decided_by, u.name as decided_by_name, d.approved_by, d.settled_at
          from engine.complaint_decisions d
          join engine.users u on u.id = d.decided_by
         where d.status = %s
         order by d.created_at
         limit %s
        """,
        (status, limit),
    )
    return await cur.fetchall()


async def approve(conn: Conn, decision_id: UUID, supervisor_id: UUID) -> None:
    decision = await _lock_decision(conn, decision_id)
    if decision["decided_by"] == supervisor_id:
        raise OutcomeNotAllowed("a different supervisor must approve this")
    if not await _open_items(conn, decision["pickup_id"]):
        raise OutcomeNotAllowed("the complaint was closed: reject this decision")
    await conn.execute(
        "update engine.complaint_decisions set approved_by = %s where id = %s",
        (supervisor_id, decision_id),
    )
    await _apply(
        conn, {**decision, "approved_by": supervisor_id}, await _pickup(conn, decision["pickup_id"])
    )


async def reject(conn: Conn, decision_id: UUID, supervisor_id: UUID) -> None:
    """The complaint stays open for another decision."""
    await _lock_decision(conn, decision_id)
    await conn.execute(
        """
        update engine.complaint_decisions
           set status = 'rejected', rejected_by = %s, settled_at = now()
         where id = %s
        """,
        (supervisor_id, decision_id),
    )
    await events.record(
        conn,
        "complaint.decision_rejected",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"decision_id": str(decision_id)},
    )


async def _lock_decision(conn: Conn, decision_id: UUID) -> dict:
    cur = await conn.execute(
        "select * from engine.complaint_decisions where id = %s for update", (decision_id,)
    )
    decision = await cur.fetchone()
    if decision is None:
        raise LookupError("decision not found")
    if decision["status"] != "pending_approval":
        raise OutcomeNotAllowed(f"this decision is already {decision['status']}")
    return decision


async def _complaint_for(
    conn: Conn, pickup_id: UUID, supervisor_id: UUID, *, required: bool
) -> None:
    """Decisions answer a household's complaint, never stand alone."""
    items = await _open_items(conn, pickup_id)
    if required and not items:
        raise OutcomeNotAllowed("there is no open complaint for this pickup")
    if any(i["assigned_to"] not in (None, supervisor_id) and i["claim_live"] for i in items):
        raise OutcomeNotAllowed("another supervisor has claimed this complaint")


async def _open_items(conn: Conn, pickup_id: UUID) -> list[dict]:
    cur = await conn.execute(
        """
        select assigned_to, claimed_at > now() - %s as claim_live
          from engine.review_items
         where pickup_id = %s and type = 'household_complaint' and status = 'open'
        """,
        (review.STALE_CLAIM, pickup_id),
    )
    return await cur.fetchall()


async def _pending_decision(conn: Conn, pickup_id: UUID) -> bool:
    cur = await conn.execute(
        "select 1 from engine.complaint_decisions"
        " where pickup_id = %s and status = 'pending_approval'",
        (pickup_id,),
    )
    return await cur.fetchone() is not None


async def _done(
    conn: Conn, pickup: dict, supervisor_id: UUID, outcome: Outcome, note: str
) -> dict[str, Any]:
    await _resolve_items(conn, pickup["id"], supervisor_id, note)
    return {"outcome": outcome, "status": "applied", "decision_id": None}


async def _resolve_items(conn: Conn, pickup_id: UUID, supervisor_id: UUID, note: str) -> None:
    cur = await conn.execute(
        """
        update engine.review_items
           set status = 'resolved', resolved_at = now(), resolved_by = %s, resolution = %s,
               assigned_to = coalesce(assigned_to, %s), claimed_at = coalesce(claimed_at, now())
         where pickup_id = %s and type = 'household_complaint' and status = 'open'
        returning id
        """,
        (supervisor_id, note, supervisor_id, pickup_id),
    )
    for item in await cur.fetchall():
        await events.record(
            conn,
            "dispute.resolved",
            actor_type="supervisor",
            actor_id=supervisor_id,
            pickup_id=pickup_id,
            payload={"review_item_id": str(item["id"])},
        )


async def _tell(conn: Conn, user_id: UUID | None, pickup_id: UUID, text: str) -> None:
    if user_id:
        await notify.queue_sms(
            conn, user_id=user_id, pickup_id=pickup_id, template="complaint", text=text
        )


async def _pickup(conn: Conn, pickup_id: UUID) -> dict[str, Any]:
    cur = await conn.execute(
        """
        select p.*, coalesce(p.completed_at, p.updated_at) as closed_at,
               h.user_id as household_user_id, r.user_id as rider_user_id
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
          left join engine.riders r on r.id = p.assigned_rider_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup is None:
        raise pickup_flow.NotYourPickup("pickup not found")
    return pickup

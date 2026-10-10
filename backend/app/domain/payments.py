"""Collecting and refunding the refuse pickup fee.

Mobile money can't be held and captured later, so the fee is collected at
request time and held in the pickup's escrow account (engine-plan.md
section 1). Every change in money is confirmed with the provider's own record,
never taken from a webhook body. Each collection is refunded on its own,
keyed by its reference, so a household that paid twice gets both back.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg_pool import AsyncConnectionPool

from app import jobs, services
from app.adapters.base import (
    ChargeStarted,
    ChargeState,
    MomoCharge,
    Network,
    PaymentEventKind,
    ProviderError,
    RefundState,
)
from app.config import get_settings
from app.db import Conn
from app.domain import events, ledger, money, pickups, review
from app.domain.pickups import Event, Status

log = logging.getLogger(__name__)

# A mobile money prompt the payer hasn't answered by now has lapsed on their
# phone, so a fresh one is allowed even if the provider still calls it pending.
PROMPT_LAPSES_AFTER = timedelta(minutes=3)
# Refund jobs keep trying for hours, not minutes, before a person is asked.
REFUND_MAX_ATTEMPTS = 12


class PaymentNotAllowed(Exception):
    pass


class RefundStillPending(Exception):
    """The provider hasn't finished the refund yet. Retry later."""


async def start_payment(
    pool: AsyncConnectionPool[Conn], *, pickup_id: UUID, household_user_id: UUID, network: Network
) -> ChargeStarted:
    """Ask the household's network to charge the fee. The charge request itself
    is never made inside a database transaction."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select 1 from engine.pickup_requests p
              join engine.households h on h.id = p.household_id
             where p.id = %s and h.user_id = %s
            """,
            (pickup_id, household_user_id),
        )
        if await cur.fetchone() is None:
            raise LookupError("pickup not found")
        pending = await _pending_collections_on(conn, pickup_id)
    # A prompt still waiting would charge twice if we sent another: settle it first.
    for reference in pending:
        async with pool.connection() as conn, conn.transaction():
            await settle_collection(conn, reference)

    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            select p.status, p.fee_pesewas, u.phone_e164
              from engine.pickup_requests p
              join engine.households h on h.id = p.household_id
              join engine.users u on u.id = h.user_id
             where p.id = %s and u.id = %s
               for no key update of p
            """,
            (pickup_id, household_user_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError("pickup not found")
        if row["status"] != Status.AWAITING_PAYMENT:
            raise PaymentNotAllowed(f"pickup is {row['status']}, not awaiting payment")
        cur = await conn.execute(
            """
            select 1 from engine.payments
             where pickup_id = %s and direction = 'collection' and status = 'pending'
               and created_at > now() - %s
            """,
            (pickup_id, PROMPT_LAPSES_AFTER),
        )
        if await cur.fetchone():
            raise PaymentNotAllowed("approve the payment prompt already sent to your phone")
        reference = f"imf_{uuid4().hex}"
        await conn.execute(
            """
            insert into engine.payments
                (pickup_id, direction, provider, reference, network, amount_pesewas)
            values (%s, 'collection', %s, %s, %s, %s)
            """,
            (pickup_id, services.get().payments.name, reference, network, row["fee_pesewas"]),
        )

    charge = MomoCharge(
        reference=reference,
        amount_pesewas=row["fee_pesewas"],
        phone_e164=row["phone_e164"],
        network=network,
        email=f"{household_user_id}@{get_settings().payer_email_domain}",
    )
    try:
        started = await services.get().payments.start_momo_charge(charge)
    except ProviderError as exc:
        if not exc.retryable:
            # Refused outright, so it never became a charge.
            await _mark_collection(pool, reference, "failed")
        raise

    if started.state is ChargeState.FAILED:
        await _mark_collection(pool, reference, "failed")
    elif started.state is ChargeState.SUCCEEDED:
        async with pool.connection() as conn, conn.transaction():
            await jobs.enqueue(conn, "payment.settle", {"reference": reference})
    return started


async def submit_otp(
    pool: AsyncConnectionPool[Conn],
    *,
    pickup_id: UUID,
    household_id: UUID,
    reference: str,
    otp: str,
) -> ChargeStarted:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select 1 from engine.payments pay
              join engine.pickup_requests p on p.id = pay.pickup_id
             where pay.reference = %s and pay.direction = 'collection' and pay.status = 'pending'
               and p.id = %s and p.household_id = %s
            """,
            (reference, pickup_id, household_id),
        )
        if await cur.fetchone() is None:
            raise LookupError("payment not found")
    return await services.get().payments.submit_otp(reference, otp)


async def settle_collection(conn: Conn, reference: str) -> None:
    """Bring a collection in line with the provider's record. Safe to run any
    number of times for the same reference."""
    cur = await conn.execute(
        """
        select pay.*, p.household_id, p.status as pickup_status, p.version
          from engine.payments pay
          join engine.pickup_requests p on p.id = pay.pickup_id
         where pay.direction = 'collection' and pay.reference = %s
           for update of pay
           for no key update of p
        """,
        (reference,),
    )
    pay = await cur.fetchone()
    if pay is None:
        raise LookupError(f"no collection with reference {reference}")
    if pay["status"] == "succeeded":
        return

    verified = await services.get().payments.verify_charge(reference)
    if verified.state is ChargeState.PENDING:
        return
    if verified.state is ChargeState.FAILED:
        await conn.execute(
            "update engine.payments set status = 'failed', updated_at = now() where id = %s",
            (pay["id"],),
        )
        return

    pickup_id = pay["pickup_id"]
    if verified.currency != "GHS" or verified.amount_pesewas != pay["amount_pesewas"]:
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=pickup_id,
            household_id=pay["household_id"],
            payload={
                "reference": reference,
                "expected": pay["amount_pesewas"],
                "received": verified.amount_pesewas,
                "currency": verified.currency,
            },
        )
        return

    cur = await conn.execute(
        """
        select count(*) as n from engine.payments
         where pickup_id = %s and direction = 'collection' and status = 'succeeded' and id <> %s
        """,
        (pickup_id, pay["id"]),
    )
    already_funded = (await cur.fetchone())["n"] > 0
    await conn.execute(
        """
        update engine.payments set status = 'succeeded', fee_pesewas = %s, updated_at = now()
         where id = %s
        """,
        (verified.fee_pesewas, pay["id"]),
    )
    hold = money.fund_hold(pickup_id, reference, verified.amount_pesewas, verified.fee_pesewas)

    if not already_funded and pay["pickup_status"] == Status.AWAITING_PAYMENT:
        await pickups.apply(
            conn,
            pickup_id,
            Event.PAYMENT_SUCCEEDED,
            expected_version=pay["version"],
            actor_type="provider",
            changes={"paid_at": pickups.NOW},
        )
        await ledger.post(conn, hold)
        await jobs.enqueue(conn, "dispatch.next", {"pickup_id": str(pickup_id)})
        await jobs.enqueue(
            conn,
            "dispatch.expire",
            {"pickup_id": str(pickup_id)},
            run_at=datetime.now(UTC) + timedelta(seconds=get_settings().dispatch_window_s),
            dedupe_key=f"dispatch-expire:{pickup_id}",
        )
    elif not already_funded and pay["pickup_status"] in pickups.TERMINAL:
        # The only payment arrived after the pickup was cancelled or expired.
        await ledger.post(conn, hold)
        await start_refund(conn, pickup_id)
    else:
        # The pickup was already paid for: hand this payment straight back.
        await ledger.post(conn, hold)
        await ledger.post(
            conn,
            money.refund_extra(pickup_id, pay["household_id"], reference, verified.amount_pesewas),
        )
        await _queue_refund(conn, reference)
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=pickup_id,
            household_id=pay["household_id"],
            payload={"reference": reference, "reason": "paid more than once, refunding"},
        )


async def start_refund(conn: Conn, pickup_id: UUID) -> bool:
    """Settle a pickup that won't happen: move what escrow holds to the household
    and refund each payment. Returns False when there is nothing to refund."""
    held = await ledger.balance(conn, money.escrow(pickup_id))
    if held == 0:
        return False
    cur = await conn.execute(
        "select household_id from engine.pickup_requests where id = %s", (pickup_id,)
    )
    household_id = (await cur.fetchone())["household_id"]
    await ledger.post(conn, money.refund_due(pickup_id, household_id, held))
    cur = await conn.execute(
        """
        select reference from engine.payments
         where pickup_id = %s and direction = 'collection' and status = 'succeeded'
        """,
        (pickup_id,),
    )
    for row in await cur.fetchall():
        await _queue_refund(conn, row["reference"])
    return True


async def _queue_refund(conn: Conn, reference: str) -> None:
    await jobs.enqueue(
        conn,
        "payment.refund",
        {"reference": reference},
        dedupe_key=f"refund:{reference}",
        max_attempts=REFUND_MAX_ATTEMPTS,
    )


async def _money_job_gave_up(conn: Conn, payload: dict, error: str) -> None:
    """A money job that stops trying must never fail quietly."""
    cur = await conn.execute(
        "select pickup_id from engine.payments where reference = %s limit 1",
        (payload.get("reference"),),
    )
    row = await cur.fetchone()
    await review.open_item(
        conn,
        "payment_mismatch",
        pickup_id=row["pickup_id"] if row else None,
        payload={
            "reference": payload.get("reference"),
            "reason": "automatic payment step gave up",
            "error": error,
        },
    )


@jobs.handler("payment.settle", on_give_up=_money_job_gave_up)
async def _settle_job(conn: Conn, payload: dict) -> None:
    await settle_collection(conn, payload["reference"])


@jobs.handler("payment.event", on_give_up=_money_job_gave_up)
async def _event_job(conn: Conn, payload: dict) -> None:
    kind = PaymentEventKind(payload["kind"])
    if kind in (PaymentEventKind.CHARGE_SUCCEEDED, PaymentEventKind.CHARGE_FAILED):
        await settle_collection(conn, payload["reference"])
    elif kind in (PaymentEventKind.REFUND_PROCESSED, PaymentEventKind.REFUND_FAILED):
        await _refund_outcome(conn, payload["reference"], payload.get("object_id"))


@jobs.handler("payment.refund", on_give_up=_money_job_gave_up)
async def _refund_job(conn: Conn, payload: dict) -> None:
    """Record the refund first, in its own job, so the row exists before the
    provider is ever called."""
    cur = await conn.execute(
        """
        select pickup_id, amount_pesewas from engine.payments
         where reference = %s and direction = 'collection' and status = 'succeeded'
        """,
        (payload["reference"],),
    )
    collection = await cur.fetchone()
    await conn.execute(
        """
        insert into engine.payments (pickup_id, direction, provider, reference, amount_pesewas)
        values (%s, 'refund', %s, %s, %s)
        on conflict (direction, reference) do nothing
        """,
        (
            collection["pickup_id"],
            services.get().payments.name,
            payload["reference"],
            collection["amount_pesewas"],
        ),
    )
    await jobs.enqueue(
        conn,
        "payment.refund_call",
        payload,
        dedupe_key=f"refund-call:{payload['reference']}",
        max_attempts=REFUND_MAX_ATTEMPTS,
    )


@jobs.handler("payment.refund_call", on_give_up=_money_job_gave_up)
async def _refund_call_job(conn: Conn, payload: dict) -> None:
    cur = await conn.execute(
        """
        select id, pickup_id, amount_pesewas, provider_refund_id from engine.payments
         where reference = %s and direction = 'refund'
           for update
        """,
        (payload["reference"],),
    )
    refund = await cur.fetchone()
    if refund["provider_refund_id"]:
        return
    # An earlier attempt may have reached the provider and lost its reply.
    # Every refund is for the whole charge, so a refunded charge means it did.
    if (await services.get().payments.verify_charge(payload["reference"])).refunded:
        await events.record(
            conn,
            "payment.refund_already_made",
            actor_type="system",
            pickup_id=refund["pickup_id"],
            payload={"reference": payload["reference"]},
        )
        return
    try:
        refund_id = await services.get().payments.refund(
            payload["reference"], refund["amount_pesewas"]
        )
    except ProviderError as exc:
        if exc.retryable:
            raise
        # Possibly refunded already by an earlier attempt whose result was lost.
        # A person checks; the refund webhook still settles it if it did go through.
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=refund["pickup_id"],
            payload={"reference": payload["reference"], "reason": f"refund refused: {exc}"},
        )
        return
    await conn.execute(
        "update engine.payments set provider_refund_id = %s, updated_at = now() where id = %s",
        (refund_id, refund["id"]),
    )
    await events.record(
        conn, "payment.refund_started", actor_type="system", pickup_id=refund["pickup_id"]
    )


async def _refund_outcome(conn: Conn, reference: str, refund_id: str | None) -> None:
    cur = await conn.execute(
        """
        select pay.id, pay.status, pay.pickup_id, pay.amount_pesewas, pay.provider_refund_id,
               p.household_id
          from engine.payments pay
          join engine.pickup_requests p on p.id = pay.pickup_id
         where pay.direction = 'refund' and pay.reference = %s
           for update of pay
        """,
        (reference,),
    )
    refund = await cur.fetchone()
    if refund is None:
        await review.open_item(
            conn,
            "payment_mismatch",
            payload={"reference": reference, "reason": "refund update for an unknown refund"},
        )
        return
    if refund["status"] != "pending":
        return
    refund_id = refund_id or refund["provider_refund_id"]
    if refund_id is None:
        # The refund call's reply was lost. The charge itself says whether it went through.
        if (await services.get().payments.verify_charge(reference)).refunded:
            await _record_refund(conn, refund, reference, succeeded=True, refund_id=None)
            return
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=refund["pickup_id"],
            payload={"reference": reference, "reason": "refund update without a refund id"},
        )
        return

    info = await services.get().payments.fetch_refund(refund_id)
    if (info.reference, info.currency, info.amount_pesewas) != (
        reference,
        "GHS",
        refund["amount_pesewas"],
    ):
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=refund["pickup_id"],
            payload={"reference": reference, "reason": "refund does not match its record"},
        )
        return
    if info.state is RefundState.PENDING:
        raise RefundStillPending(f"refund {refund_id} is still pending")
    await _record_refund(
        conn, refund, reference, succeeded=info.state is RefundState.SUCCEEDED, refund_id=refund_id
    )


async def _record_refund(
    conn: Conn, refund: dict, reference: str, *, succeeded: bool, refund_id: str | None
) -> None:
    await conn.execute(
        """
        update engine.payments
           set status = %s, provider_refund_id = coalesce(%s, provider_refund_id),
               updated_at = now()
         where id = %s
        """,
        ("succeeded" if succeeded else "failed", refund_id, refund["id"]),
    )
    if succeeded:
        await ledger.post(
            conn,
            money.refund_paid(
                refund["pickup_id"], refund["household_id"], reference, refund["amount_pesewas"]
            ),
        )
    else:
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=refund["pickup_id"],
            household_id=refund["household_id"],
            payload={"reference": reference, "reason": "refund failed"},
        )


@jobs.handler("payment.expire")
async def _expire_job(conn: Conn, payload: dict) -> None:
    """The payment window ended. Check pending charges with the provider first,
    in case a webhook was lost, then cancel if still unpaid."""
    pickup_id = UUID(payload["pickup_id"])
    for reference in await _pending_collections_on(conn, pickup_id):
        try:
            async with conn.transaction():
                await settle_collection(conn, reference)
        except Exception as exc:
            # Cancel anyway: money that turns up later is refunded automatically.
            log.warning("payment.expire: could not settle %s: %s", reference, jobs.describe(exc))

    cur = await conn.execute(
        "select status, version from engine.pickup_requests where id = %s", (pickup_id,)
    )
    pickup = await cur.fetchone()
    if pickup["status"] == Status.AWAITING_PAYMENT:
        await pickups.apply(
            conn,
            pickup_id,
            Event.PAYMENT_FAILED,
            expected_version=pickup["version"],
            actor_type="system",
            payload={"reason": "payment window ended"},
        )


async def _pending_collections_on(conn: Conn, pickup_id: UUID) -> list[str]:
    cur = await conn.execute(
        """
        select reference from engine.payments
         where pickup_id = %s and direction = 'collection' and status = 'pending'
        """,
        (pickup_id,),
    )
    return [row["reference"] for row in await cur.fetchall()]


async def _mark_collection(pool: AsyncConnectionPool[Conn], reference: str, status: str) -> None:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            """
            update engine.payments set status = %s, updated_at = now()
             where direction = 'collection' and reference = %s
            """,
            (status, reference),
        )

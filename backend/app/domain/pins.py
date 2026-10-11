"""Pickup confirmation PINs (engine-design.md section 3).

A PIN is issued once the rider has collected, sent to the household by SMS,
and stored only as an HMAC. Confirming it completes the pickup and releases
escrow in the same database transaction.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

from app import jobs
from app.config import get_settings
from app.db import Conn
from app.domain import events, ledger, money, notify, payments, pickups, review
from app.domain.pickups import Event, Status

PIN_DIGITS = 6
# Wrong tries allowed per user per hour, across all their pickups.
HOURLY_ATTEMPT_LIMIT = 10
# PINs a pickup can be sent in total: the first one plus resends.
MAX_PINS_PER_PICKUP = 4


class PinUnavailable(Exception):
    """No live PIN: not issued yet, expired, or already used."""


class PinLocked(Exception):
    pass


class TooManyAttempts(Exception):
    pass


@dataclass(frozen=True)
class PinResult:
    confirmed: bool
    tries_left: int = 0
    locked: bool = False


def generate_pin() -> str:
    while True:
        pin = "".join(secrets.choice("0123456789") for _ in range(PIN_DIGITS))
        if not _is_trivial(pin):
            return pin


def _is_trivial(pin: str) -> bool:
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:], strict=False)}
    return steps <= {0} or steps == {1} or steps == {-1}


def pin_hmac(pickup_id: UUID, pin: str) -> str:
    pepper = get_settings().pin_pepper.get_secret_value().encode()
    return hmac.new(pepper, f"{pickup_id}:{pin}".encode(), hashlib.sha256).hexdigest()


async def _issue_gave_up(conn: Conn, payload: dict, error: str) -> None:
    await review.open_item(
        conn,
        "pin_issue",
        pickup_id=UUID(payload["pickup_id"]),
        payload={"reason": "PIN could not be sent", "error": error},
    )


@jobs.handler("pin.issue", on_give_up=_issue_gave_up)
async def issue(conn: Conn, payload: dict) -> None:
    """Generate a PIN and text it to the household, in one job, so the PIN is
    never stored anywhere in plain text."""
    pickup_id = UUID(payload["pickup_id"])
    # Lock live PINs before reading the pickup: a confirm in flight holds them,
    # so we wait for it and then see the pickup completed.
    await conn.execute(
        "select id from engine.pins where pickup_id = %s and status in ('active', 'locked')"
        " for update",
        (pickup_id,),
    )
    cur = await conn.execute(
        """
        select p.status, p.fee_pesewas, h.user_id
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup["status"] != Status.AWAITING_PIN:
        return

    await conn.execute(
        """
        update engine.pins set status = 'superseded'
         where pickup_id = %s and status in ('active', 'locked')
        """,
        (pickup_id,),
    )
    pin = generate_pin()
    cur = await conn.execute(
        """
        insert into engine.pins (pickup_id, pin_hmac, expires_at)
        values (%s, %s, now() + make_interval(secs => %s))
        returning id, expires_at
        """,
        (pickup_id, pin_hmac(pickup_id, pin), get_settings().pin_ttl_s),
    )
    issued = await cur.fetchone()
    # Each PIN gets its own deadline, so a resent PIN isn't cut short.
    await _expire_at(conn, pickup_id, issued)
    fee = f"GHS {pickup['fee_pesewas'] / 100:.2f}" if pickup["fee_pesewas"] else "your points"
    text = (
        f"Imfucuza: your rider has collected your waste. Your PIN is {pin}. "
        f"Give it only once the pickup is done: it releases {fee}. "
        "If you get more than one PIN, use the latest."
    )
    sent = await notify.send_now(
        conn,
        user_id=pickup["user_id"],
        pickup_id=pickup_id,
        template="pin",
        text=text,
        masked=text.replace(pin, "*" * PIN_DIGITS),
    )
    if sent is None:
        # The household never got it. Don't leave them to wait out the 24 hours.
        await review.open_item(
            conn, "pin_issue", pickup_id=pickup_id, payload={"reason": "PIN SMS refused"}
        )
    await events.record(conn, "pin.issued", actor_type="system", pickup_id=pickup_id)


async def request_resend(conn: Conn, pickup_id: UUID, *, household_id: UUID) -> None:
    """Send a fresh PIN, replacing the old one. Capped, so it can't be used to
    spray SMS or reset the attempt count indefinitely."""
    cur = await conn.execute(
        """
        select p.status,
               (select count(*) from engine.pins where pickup_id = p.id) as issued,
               exists (select 1 from engine.pins where pickup_id = p.id and status = 'locked')
                 as locked
          from engine.pickup_requests p
         where p.id = %s and p.household_id = %s
        """,
        (pickup_id, household_id),
    )
    pickup = await cur.fetchone()
    if pickup is None or pickup["status"] != Status.AWAITING_PIN:
        raise PinUnavailable("this pickup is not waiting for a PIN")
    # A new PIN would reset the attempt count: only a supervisor clears a lock.
    if pickup["locked"]:
        raise PinLocked("this PIN is locked: a supervisor will follow up")
    if pickup["issued"] >= MAX_PINS_PER_PICKUP:
        raise TooManyAttempts("no more PINs can be sent for this pickup")
    await jobs.enqueue(
        conn,
        "pin.issue",
        {"pickup_id": str(pickup_id)},
        dedupe_key=f"pin-issue:{pickup_id}:resend-{pickup['issued']}",
        max_attempts=10,
    )


async def confirm(
    conn: Conn, pickup_id: UUID, pin: str, *, via: str, actor_type: str, actor_user_id: UUID
) -> PinResult:
    """Check a PIN. A wrong attempt is recorded and returned, not raised, so the
    caller commits it: raising would roll the attempt count back."""
    settings = get_settings()
    cur = await conn.execute(
        """
        select count(*) as n from engine.events
         where name = 'pin.failed' and actor_id = %s and occurred_at > now() - interval '1 hour'
        """,
        (actor_user_id,),
    )
    if (await cur.fetchone())["n"] >= HOURLY_ATTEMPT_LIMIT:
        raise TooManyAttempts("too many wrong PINs in the last hour")

    cur = await conn.execute(
        """
        select id, pin_hmac, status, attempts, expires_at <= now() as is_expired
          from engine.pins
         where pickup_id = %s and status in ('active', 'locked')
           for update
        """,
        (pickup_id,),
    )
    live = await cur.fetchone()
    if live is None or live["is_expired"]:
        raise PinUnavailable("no PIN is waiting for this pickup")
    if live["status"] == "locked":
        raise PinLocked("this PIN is locked: a supervisor will follow up")

    if hmac.compare_digest(live["pin_hmac"], pin_hmac(pickup_id, pin)):
        await conn.execute(
            """
            update engine.pins set status = 'confirmed', confirmed_at = now(), confirmed_via = %s
             where id = %s
            """,
            (via, live["id"]),
        )
        await _complete(
            conn, pickup_id, via=via, actor_type=actor_type, actor_user_id=actor_user_id
        )
        return PinResult(confirmed=True)

    attempts = live["attempts"] + 1
    locked = attempts >= settings.pin_max_attempts
    await conn.execute(
        "update engine.pins set attempts = %s, status = %s where id = %s",
        (attempts, "locked" if locked else "active", live["id"]),
    )
    await events.record(
        conn,
        "pin.failed",
        actor_type=actor_type,
        actor_id=actor_user_id,
        pickup_id=pickup_id,
        payload={"attempts": attempts, "via": via},
    )
    if locked:
        await events.record(conn, "pin.locked", actor_type="system", pickup_id=pickup_id)
        await review.open_item(
            conn, "pin_issue", pickup_id=pickup_id, payload={"reason": "too many wrong PINs"}
        )
    return PinResult(
        confirmed=False, tries_left=max(settings.pin_max_attempts - attempts, 0), locked=locked
    )


async def _complete(
    conn: Conn, pickup_id: UUID, *, via: str, actor_type: str, actor_user_id: UUID
) -> None:
    cur = await conn.execute(
        """
        select version, assigned_rider_id, offering, fee_pesewas
          from engine.pickup_requests where id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    await pickups.apply(
        conn,
        pickup_id,
        Event.PIN_CONFIRMED,
        expected_version=pickup["version"],
        actor_type=actor_type,
        actor_id=actor_user_id,
        payload={"confirmed_via": via},
    )
    held = await ledger.balance(conn, money.escrow(pickup_id))
    if pickup["offering"] == "refuse" and held != pickup["fee_pesewas"]:
        await review.open_item(
            conn,
            "payment_mismatch",
            pickup_id=pickup_id,
            payload={"reason": "escrow differs from the fee at release", "held": held},
        )
    if held:
        await ledger.post(
            conn,
            money.release(
                pickup_id, pickup["assigned_rider_id"], held, get_settings().rider_share_percent
            ),
        )


async def _expire_at(conn: Conn, pickup_id: UUID, pin: dict) -> None:
    await jobs.enqueue(
        conn,
        "pin.expire",
        {"pickup_id": str(pickup_id)},
        run_at=pin["expires_at"],
        dedupe_key=f"pin-expire:{pickup_id}:{pin['id']}",
    )


async def _expire_gave_up(conn: Conn, payload: dict, error: str) -> None:
    await review.open_item(
        conn,
        "pin_issue",
        pickup_id=UUID(payload["pickup_id"]),
        payload={"reason": "PIN expiry failed", "error": error},
    )


@jobs.handler("pin.expire", on_give_up=_expire_gave_up)
async def _expire_job(conn: Conn, payload: dict) -> None:
    """Nobody confirmed in time: refund, and let a supervisor look into it."""
    pickup_id = UUID(payload["pickup_id"])
    # Lock the PINs first, as confirm does, then look again in a new statement:
    # that one sees a PIN a resend has just committed.
    await conn.execute(
        "select id from engine.pins where pickup_id = %s and status in ('active', 'locked')"
        " for update",
        (pickup_id,),
    )
    cur = await conn.execute(
        """
        select id, expires_at from engine.pins
         where pickup_id = %s and status in ('active', 'locked') and expires_at > now()
        """,
        (pickup_id,),
    )
    live = await cur.fetchone()
    cur = await conn.execute(
        """
        select p.status, p.version, p.household_id, h.user_id
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup["status"] != Status.AWAITING_PIN:
        return
    if live:
        # A newer PIN is still valid. Make sure its own timer is there to end it.
        await _expire_at(conn, pickup_id, live)
        return
    await conn.execute(
        """
        update engine.pins set status = 'expired'
         where pickup_id = %s and status in ('active', 'locked')
        """,
        (pickup_id,),
    )
    await pickups.apply(
        conn,
        pickup_id,
        Event.PIN_EXPIRED,
        expected_version=pickup["version"],
        actor_type="system",
    )
    refunded = await payments.start_refund(conn, pickup_id)
    await review.open_item(
        conn,
        "pin_issue",
        pickup_id=pickup_id,
        household_id=pickup["household_id"],
        payload={"reason": "pickup never confirmed"},
    )
    await notify.queue_sms(
        conn,
        user_id=pickup["user_id"],
        pickup_id=pickup_id,
        template="unconfirmed",
        text="Imfucuza: your pickup was never confirmed with its PIN."
        + (" Your payment is being refunded." if refunded else "")
        + " A supervisor will follow up.",
    )

"""SMS to households and riders, with delivery tracking (signal S5)."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import jobs, services
from app.adapters.base import DeliveryState, ProviderError
from app.db import Conn
from app.domain import events

log = logging.getLogger(__name__)

STATUS_CHECK_AFTER = timedelta(minutes=2)


async def queue_sms(
    conn: Conn,
    *,
    user_id: UUID,
    template: str,
    text: str,
    pickup_id: UUID | None = None,
    offer_id: UUID | None = None,
) -> None:
    """Send later, from a job, so a slow provider never holds up a request.
    Never queue a secret this way: job payloads are stored in plain text."""
    await jobs.enqueue(
        conn,
        "sms.send",
        {
            "user_id": str(user_id),
            "pickup_id": str(pickup_id) if pickup_id else None,
            "offer_id": str(offer_id) if offer_id else None,
            "template": template,
            "text": text,
        },
    )


async def send_now(
    conn: Conn,
    *,
    user_id: UUID,
    template: str,
    text: str,
    masked: str | None = None,
    pickup_id: UUID | None = None,
    offer_id: UUID | None = None,
) -> UUID | None:
    """Send inside the caller's job. `masked` is what gets stored when the text
    carries a secret. Returns the notification id, or None if the provider
    refused the message for good."""
    cur = await conn.execute("select phone_e164 from engine.users where id = %s", (user_id,))
    phone = (await cur.fetchone())["phone_e164"]
    gateway = services.get().sms
    try:
        sent = await gateway.send(phone, text)
    except ProviderError as exc:
        if exc.retryable:
            raise
        log.error("SMS %s to user %s refused: %s", template, user_id, exc)
        await events.record(
            conn,
            "sms.refused",
            actor_type="system",
            pickup_id=pickup_id,
            payload={"template": template, "user_id": str(user_id)},
        )
        return None

    cur = await conn.execute(
        """
        insert into engine.notifications
            (user_id, pickup_id, channel, template, body_masked, provider, provider_message_id)
        values (%s, %s, 'sms', %s, %s, %s, %s)
        returning id
        """,
        (user_id, pickup_id, template, masked or text, gateway.name, sent.provider_message_id),
    )
    notification_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "sms.sent",
        actor_type="system",
        pickup_id=pickup_id,
        payload={"notification_id": str(notification_id), "template": template},
    )
    await jobs.enqueue(
        conn,
        "sms.check_status",
        {"notification_id": str(notification_id), "offer_id": str(offer_id) if offer_id else None},
        run_at=datetime.now(UTC) + STATUS_CHECK_AFTER,
        dedupe_key=f"sms-status:{notification_id}",
    )
    return notification_id


@jobs.handler("sms.send")
async def _send_job(conn: Conn, payload: dict) -> None:
    await send_now(
        conn,
        user_id=UUID(payload["user_id"]),
        template=payload["template"],
        text=payload["text"],
        pickup_id=UUID(payload["pickup_id"]) if payload.get("pickup_id") else None,
        offer_id=UUID(payload["offer_id"]) if payload.get("offer_id") else None,
    )


@jobs.handler("sms.check_status")
async def _check_status_job(conn: Conn, payload: dict) -> None:
    from app.domain import dispatch  # dispatch imports this module

    cur = await conn.execute(
        "select provider_message_id, pickup_id from engine.notifications where id = %s",
        (payload["notification_id"],),
    )
    row = await cur.fetchone()
    state = await services.get().sms.delivery_state(row["provider_message_id"])
    status = {
        DeliveryState.DELIVERED: "delivered",
        DeliveryState.FAILED: "failed",
        DeliveryState.QUEUED: "sent",
        DeliveryState.UNKNOWN: "unknown",
    }[state]
    await conn.execute(
        "update engine.notifications set status = %s, status_checked_at = now() where id = %s",
        (status, payload["notification_id"]),
    )
    await events.record(
        conn,
        "sms.status",
        actor_type="provider",
        pickup_id=row["pickup_id"],
        payload={"notification_id": payload["notification_id"], "status": status},
    )
    if state is DeliveryState.FAILED and payload.get("offer_id"):
        await dispatch.offer_undeliverable(conn, UUID(payload["offer_id"]))

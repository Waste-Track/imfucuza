"""Inbound calls from providers. None of them carry a user token, so each one
proves itself with a signature before anything changes."""

import base64
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import jobs, services
from app.adapters.base import InvalidWebhook, ProviderError
from app.config import get_settings
from app.db import ConnDep
from app.domain import events, people

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"], include_in_schema=False)

# Standard Webhooks: reject signatures older or newer than this.
SIGNATURE_TOLERANCE_S = 5 * 60


@router.post("/webhooks/payments/{provider}")
async def payment_webhook(provider: str, request: Request, conn: ConnDep) -> JSONResponse:
    gateway = services.get().payments
    if provider != gateway.name:
        return JSONResponse(status_code=404, content={"detail": "unknown provider"})
    body = await request.body()
    try:
        event = gateway.parse_webhook(body, request.headers)
    except InvalidWebhook as exc:
        # Logged, not stored: an unauthenticated caller must not be able to
        # write to the database (signal S8 comes from these log lines).
        log.warning("webhook.rejected provider=%s reason=%s", provider, exc)
        return JSONResponse(status_code=401, content={"detail": "invalid signature"})

    if not await _first_delivery(conn, provider, event.event_id, event.kind, event.reference):
        await events.record(
            conn, "webhook.duplicate", actor_type="provider", payload={"provider": provider}
        )
        return JSONResponse({"status": "duplicate"})
    await jobs.enqueue(
        conn,
        "payment.event",
        {"kind": event.kind, "reference": event.reference, "object_id": event.provider_object_id},
    )
    return JSONResponse({"status": "queued"})


@router.post("/hooks/supabase/send-sms")
async def supabase_send_sms(request: Request, conn: ConnDep) -> JSONResponse:
    """Supabase Auth's Send SMS hook: sign-in codes go out through our SMS
    gateway instead of Supabase's built-in providers."""
    body = await request.body()
    if not _valid_standard_webhook(body, request.headers):
        log.warning("webhook.rejected provider=supabase_sms")
        return JSONResponse(status_code=401, content={"error": {"message": "invalid signature"}})
    # A captured call replayed within the tolerance must not send another SMS.
    # The id is recorded only once the code is sent, so a retry after a failed
    # send still goes out.
    msg_id = request.headers["webhook-id"]
    cur = await conn.execute(
        "select 1 from engine.inbound_webhooks where provider = 'supabase_sms' and event_id = %s",
        (msg_id,),
    )
    if await cur.fetchone():
        return JSONResponse({})

    payload = await request.json()
    try:
        phone = people.normalize_msisdn(payload["user"]["phone"])
        otp = payload["sms"]["otp"]
    except (KeyError, TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": {"message": "bad payload"}})
    try:
        await services.get().sms.send(
            phone, f"Your Imfucuza sign-in code is {otp}. Do not share it with anyone."
        )
    except ProviderError:
        return JSONResponse(status_code=502, content={"error": {"message": "SMS unavailable"}})
    await _first_delivery(conn, "supabase_sms", msg_id, "send_sms", msg_id)
    return JSONResponse({})


async def _first_delivery(conn, provider: str, event_id: str, kind: str, reference: str) -> bool:
    cur = await conn.execute(
        """
        insert into engine.inbound_webhooks (provider, event_id, kind, reference)
        values (%s, %s, %s, %s)
        on conflict (provider, event_id) do nothing
        returning id
        """,
        (provider, event_id, kind, reference),
    )
    return await cur.fetchone() is not None


def _valid_standard_webhook(body: bytes, headers) -> bool:
    """https://www.standardwebhooks.com: HMAC-SHA256 over "id.timestamp.body",
    keyed with the base64 part of the "v1,whsec_..." secret."""
    secret = get_settings().supabase_sms_hook_secret.get_secret_value()
    msg_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signatures = headers.get("webhook-signature", "")
    if not (secret and msg_id and timestamp and signatures):
        return False
    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_S:
            return False
        key = base64.b64decode(secret.removeprefix("v1,").removeprefix("whsec_"))
    except ValueError:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        version == "v1" and hmac.compare_digest(sig.encode(), expected.encode())
        for version, _, sig in (part.partition(",") for part in signatures.split())
    )

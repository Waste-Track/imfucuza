"""USSD callbacks. Providers don't sign them, so the path carries a secret
token, and each session is bound to the phone number that started it."""

import hmac
import ipaddress
import logging

from fastapi import APIRouter, Request, Response

from app.adapters.ussd_base import InvalidUssdRequest, UssdReply
from app.adapters.ussd_codecs import CODECS
from app.config import get_settings
from app.db import ConnDep
from app.domain import people, ussd

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"], include_in_schema=False)


@router.post("/webhooks/ussd/{provider}/{token}")
async def ussd_callback(provider: str, token: str, request: Request, conn: ConnDep) -> Response:
    settings = get_settings()
    expected = settings.ussd_webhook_token.get_secret_value()
    codec = CODECS.get(provider) if provider == settings.ussd_provider else None
    # A wrong token or provider looks exactly like a route that doesn't exist.
    if not expected or codec is None or not hmac.compare_digest(token.encode(), expected.encode()):
        return Response(status_code=404)
    if settings.ussd_allowed_ips and not _caller_allowed(request, settings.ussd_allowed_ips):
        log.warning("ussd.rejected provider=%s reason=caller not allowed", provider)
        return Response(status_code=404)

    try:
        req = codec.parse(await request.body(), request.headers.get("content-type", ""))
    except InvalidUssdRequest as exc:
        log.warning("ussd.rejected provider=%s reason=%s", provider, exc)
        return Response(status_code=400)

    try:
        phone = people.normalize_msisdn(req.msisdn)
    except ValueError:
        reply = UssdReply(ussd.NOT_REGISTERED, end=True)
    else:
        reply = await ussd.handle(conn, req, phone)
    body, media_type = codec.render(req, reply)
    return Response(content=body, media_type=media_type)


def _caller_allowed(request: Request, allowed: list[str]) -> bool:
    forwarded = request.headers.get("x-forwarded-for", "")
    if get_settings().trust_forwarded_for and forwarded:
        # The proxy appends the address it saw; earlier entries are the caller's to forge.
        caller = forwarded.split(",")[-1].strip()
    else:
        caller = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(caller)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(net, strict=False) for net in allowed)

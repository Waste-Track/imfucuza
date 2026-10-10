"""USSD codecs: Africa's Talking, Arkesel and mNotify (BMS).

Content-Type is ignored. Gateways don't always set it, and every body is
checked field by field anyway.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote

from app.adapters.ussd_base import InvalidUssdRequest, UssdCodec, UssdReply, UssdRequest


class AfricasTalkingCodec:
    """Form POST in, plain text out.

    Docs: https://developers.africastalking.com/docs/ussd/overview
    """

    name = "africastalking"

    def parse(self, body: bytes, content_type: str) -> UssdRequest:
        try:
            fields = parse_qs(body.decode(), keep_blank_values=True, errors="strict")
        except ValueError:
            raise InvalidUssdRequest("body is not a form") from None
        session_id = _not_blank("sessionId", _form_field(fields, "sessionId"))
        msisdn = _not_blank("phoneNumber", _form_field(fields, "phoneNumber"))
        # Every input so far, joined by "*", so its length is the step count.
        text = _form_field(fields, "text")
        step = len(text.split("*")) if text else 0
        return UssdRequest(self.name, session_id, msisdn, text.rsplit("*", 1)[-1], text == "", step)

    def render(self, request: UssdRequest, reply: UssdReply) -> tuple[bytes, str]:
        prefix = "END " if reply.end else "CON "
        return (prefix + reply.text).encode(), "text/plain"


class ArkeselCodec:
    """JSON in, JSON out.

    Spec: https://developers.arkesel.com/spec/api_spec.v2.4.0.yaml (USSDREQUEST, USSDRESPONSE)
    """

    name = "arkesel"

    def parse(self, body: bytes, content_type: str) -> UssdRequest:
        payload = _json_object(body)
        session_id = _not_blank("sessionID", _string(payload, "sessionID"))
        user_id = _not_blank("userID", _string(payload, "userID"))
        msisdn = _not_blank("msisdn", _string(payload, "msisdn"))
        user_data = _string(payload, "userData")
        new_session = payload.get("newSession")
        if not isinstance(new_session, bool):
            raise InvalidUssdRequest("newSession is missing or not a boolean")
        return UssdRequest(
            self.name,
            # The reply must echo userID, and UssdRequest has nowhere else to keep it.
            _pack(user_id, session_id),
            msisdn,
            "" if new_session else user_data,
            new_session,
        )

    def render(self, request: UssdRequest, reply: UssdReply) -> tuple[bytes, str]:
        user_id, session_id = _unpack(request.session_id)
        return _json(
            {
                "sessionID": session_id,
                "userID": user_id,
                "msisdn": request.msisdn,
                "message": reply.text,
                "continueSession": not reply.end,
            }
        )


class MnotifyCodec:
    """Shared-code USSD: JSON in, JSON out.

    Spec: https://developer.bms.africa/openapi14.yaml ("Shared USSD"). The
    dedicated-code callback (sessId, text, ussdGwId) is a different format.
    """

    name = "mnotify"

    def __init__(self, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now

    def parse(self, body: bytes, content_type: str) -> UssdRequest:
        payload = _json_object(body)
        msisdn = _not_blank("msisdn", _string(payload, "msisdn"))
        sequence_id = _not_blank("sequenceID", _string(payload, "sequenceID"))
        data = _string(payload, "data")
        # UNVERIFIED: the spec has no new-session flag. Its one example, a first
        # request, carries the dialled code, and later `data` is read as the
        # latest input only.
        is_new = data.startswith("*") and data.endswith("#")
        return UssdRequest(
            self.name,
            # UNVERIFIED that sequenceID is unique across subscribers, so scope it to one.
            _pack(msisdn, sequence_id),
            msisdn,
            "" if is_new else data,
            is_new,
        )

    def render(self, request: UssdRequest, reply: UssdReply) -> tuple[bytes, str]:
        _, sequence_id = _unpack(request.session_id)
        # The spec asks for "\r\n" line breaks. That adds at most one character per
        # line, which still fits the networks' 182-character screen.
        return _json(
            {
                "msisdn": request.msisdn,
                "sequenceID": sequence_id,
                "message": reply.text.replace("\n", "\r\n"),
                "timestamp": self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "continueFlag": 1 if reply.end else 0,
            }
        )


CODECS: dict[str, UssdCodec] = {
    codec.name: codec for codec in (AfricasTalkingCodec(), ArkeselCodec(), MnotifyCodec())
}


def _form_field(fields: Mapping[str, list[str]], name: str) -> str:
    values = fields.get(name)
    if values is None:
        raise InvalidUssdRequest(f"missing {name}")
    if len(values) != 1:
        raise InvalidUssdRequest(f"{name} sent {len(values)} times")
    return values[0]


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise InvalidUssdRequest("body is not JSON") from None
    if not isinstance(payload, dict):
        raise InvalidUssdRequest("body is not a JSON object")
    return payload


def _string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise InvalidUssdRequest(f"{name} is missing or not a string")
    try:
        value.encode()
    except UnicodeEncodeError:
        raise InvalidUssdRequest(f"{name} is not valid text") from None
    return value


def _not_blank(name: str, value: str) -> str:
    if not value.strip():
        raise InvalidUssdRequest(f"{name} is blank")
    return value


def _pack(*ids: str) -> str:
    """Several ids as one session id that _unpack splits back exactly."""
    return ":".join(quote(part, safe="") for part in ids)


def _unpack(session_id: str) -> list[str]:
    return [unquote(part) for part in session_id.split(":")]


def _json(payload: dict[str, Any]) -> tuple[bytes, str]:
    return json.dumps(payload, separators=(",", ":")).encode(), "application/json"

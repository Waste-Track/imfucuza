"""mNotify (now BMS) SMS gateway.

Spec: https://developer.bms.africa/openapi14.yaml. The API is still served from
api.mnotify.com and takes the API key as a `key` query parameter, so every
request URL holds the key.
"""

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.adapters.base import DeliveryState, ProviderError, SmsSent

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.mnotify.com/api"
TIMEOUT_S = 15.0
MAX_SENDER_ID_LENGTH = 11

# The report statuses the spec lists. Anything else is unknown.
_DELIVERY_STATES = {
    "delivered": DeliveryState.DELIVERED,
    "submitted": DeliveryState.QUEUED,
    "undelivered": DeliveryState.FAILED,
    "failed": DeliveryState.FAILED,
    "rejected": DeliveryState.FAILED,
}

_GHANA_E164 = re.compile(r"\+233(\d{9})")
_KEY_PARAM = re.compile(r"([?&]key=)[^&#\s\"'>]+")


def _redact_key(text: str) -> str:
    return _KEY_PARAM.sub(r"\1***", text)


class _RedactKeyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _redact_key(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


# httpx logs every request URL at INFO, and ours carry the API key.
logging.getLogger("httpx").addFilter(_RedactKeyFilter())


class MnotifyGateway:
    name = "mnotify"

    def __init__(
        self,
        api_key: str,
        *,
        sender_id: str,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("mnotify needs an API key")
        if not 1 <= len(sender_id) <= MAX_SENDER_ID_LENGTH:
            raise ValueError(f"sender id must be 1 to {MAX_SENDER_ID_LENGTH} characters")
        self._api_key = api_key
        self._sender_id = sender_id
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)

    async def send(self, to_e164: str, text: str) -> SmsSent:
        """Returns the campaign id, which is what the delivery report is looked up by."""
        payload = await self._call(
            "POST",
            "/sms/quick",
            {
                "recipient": [_local_number(to_e164)],
                "sender": self._sender_id,
                "message": text,
                "is_schedule": False,
                "schedule_date": "",
            },
        )
        summary = payload.get("summary")
        campaign_id = summary.get("_id") if isinstance(summary, dict) else None
        if campaign_id in (None, ""):
            # The text has probably gone out, so a retry would send it twice.
            raise self._error("mnotify POST /sms/quick: no campaign id", retryable=False)
        return SmsSent(str(campaign_id))

    async def delivery_state(self, provider_message_id: str) -> DeliveryState:
        path = f"/campaign/{quote(provider_message_id, safe='')}"
        payload = await self._call("GET", path)
        report = payload.get("report")
        if not isinstance(report, list) or not report or not isinstance(report[0], dict):
            return DeliveryState.UNKNOWN
        # One recipient per campaign, so one report entry.
        status = str(report[0].get("status") or "").lower()
        state = _DELIVERY_STATES.get(status, DeliveryState.UNKNOWN)
        if state is DeliveryState.UNKNOWN:
            log.warning("mnotify campaign %s: unexpected status %r", provider_message_id, status)
        return state

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        where = f"mnotify {method} {path}"
        # Errors are raised `from None`: httpx exceptions keep the request, key and all.
        try:
            response = await self._client.request(
                method,
                self._base_url + path,
                params={"key": self._api_key},
                json=body,
                timeout=TIMEOUT_S,
            )
        except httpx.TimeoutException:
            raise self._error(f"{where}: timed out") from None
        except httpx.HTTPError as exc:
            raise self._error(f"{where}: {type(exc).__name__}: {exc}") from None

        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = None
        message = payload.get("message") if isinstance(payload, dict) else None
        detail = f"{where}: HTTP {status}" + (f" {message}" if message else "")
        if status >= 500 or status == 429:
            raise self._error(detail)
        if status >= 400:
            raise self._error(detail, retryable=False)
        # Not retried: a send may have gone out even when the reply is odd.
        if not isinstance(payload, dict):
            raise self._error(f"{detail}, body is not JSON", retryable=False)
        if payload.get("status") != "success":
            raise self._error(detail, retryable=False)
        return payload

    def _error(self, message: str, *, retryable: bool = True) -> ProviderError:
        message = _redact_key(message).replace(self._api_key, "***")
        return ProviderError(message, retryable=retryable)


def _local_number(phone_e164: str) -> str:
    """+233241234567 -> 0241234567, the recipient format the spec uses."""
    match = _GHANA_E164.fullmatch(phone_e164)
    if match is None:
        raise ProviderError("mnotify sends to +233 phone numbers only", retryable=False)
    return "0" + match.group(1)

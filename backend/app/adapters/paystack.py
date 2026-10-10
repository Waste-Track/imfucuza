"""Paystack: Ghana mobile money charges, refunds and webhooks.

Amounts are pesewas on both sides. API reference: https://paystack.com/docs/api/
"""

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.adapters.base import (
    ChargeStarted,
    ChargeState,
    InvalidWebhook,
    MomoCharge,
    Network,
    PaymentEvent,
    PaymentEventKind,
    ProviderError,
    RefundInfo,
    RefundState,
    VerifiedCharge,
)

log = logging.getLogger(__name__)

TIMEOUT_S = 15.0

# From GET /bank?currency=GHS&type=mobile_money. Telecel kept Vodafone's code.
PROVIDER_CODES = {
    Network.MTN: "mtn",
    Network.TELECEL: "vod",
    Network.AIRTELTIGO: "atl",
}

# Charge statuses we expect from a Ghana mobile money charge.
_MOMO_STATUSES = {"pending", "pay_offline", "send_otp", "success", "failed"}
# Every other charge status, `timeout` included, stays pending until verify_charge settles it.
_CHARGE_STATES = {"success": ChargeState.SUCCEEDED, "failed": ChargeState.FAILED}

# `abandoned` can still turn into `success`, so like every other status it stays pending.
_TRANSACTION_STATES = {
    "success": ChargeState.SUCCEEDED,
    "failed": ChargeState.FAILED,
    "reversed": ChargeState.FAILED,
}

_REFUND_STATES = {"processed": RefundState.SUCCEEDED, "failed": RefundState.FAILED}

_EVENT_KINDS = {
    "charge.success": PaymentEventKind.CHARGE_SUCCEEDED,
    "refund.processed": PaymentEventKind.REFUND_PROCESSED,
    "refund.failed": PaymentEventKind.REFUND_FAILED,
}

_GHANA_E164 = re.compile(r"\+233(\d{9})")


class PaystackProvider:
    name = "paystack"

    def __init__(
        self,
        secret_key: str,
        *,
        base_url: str = "https://api.paystack.co",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # It also signs webhooks, so an empty key would let anyone forge one.
        if not secret_key:
            raise ValueError("paystack needs a secret key")
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)

    async def start_momo_charge(self, charge: MomoCharge) -> ChargeStarted:
        data = await self._call(
            "POST",
            "/charge",
            {
                "email": charge.email,
                "amount": charge.amount_pesewas,
                "currency": "GHS",
                "reference": charge.reference,
                "mobile_money": {
                    "phone": _local_number(charge.phone_e164),
                    "provider": PROVIDER_CODES[charge.network],
                },
            },
        )
        return _charge_started(charge.reference, data)

    async def submit_otp(self, reference: str, otp: str) -> ChargeStarted:
        data = await self._call("POST", "/charge/submit_otp", {"otp": otp, "reference": reference})
        return _charge_started(reference, data)

    async def verify_charge(self, reference: str) -> VerifiedCharge:
        path = f"/transaction/verify/{quote(reference, safe='')}"
        try:
            data = await self._call("GET", path)
        except ProviderError as exc:
            # A charge that never reached Paystack can't succeed later.
            if not exc.retryable and "not found" in str(exc).lower():
                return VerifiedCharge(reference, ChargeState.FAILED, 0, 0, "GHS")
            raise
        amount, fee, currency = data.get("amount"), data.get("fees") or 0, data.get("currency")
        if type(amount) is not int or type(fee) is not int or not isinstance(currency, str):
            raise self._error(f"paystack GET {path}: unexpected amount, fees or currency")
        status = str(data.get("status"))
        state = _TRANSACTION_STATES.get(status, ChargeState.PENDING)
        return VerifiedCharge(reference, state, amount, fee, currency, status == "reversed")

    async def refund(self, reference: str, amount_pesewas: int) -> str:
        data = await self._call(
            "POST", "/refund", {"transaction": reference, "amount": amount_pesewas}
        )
        if data.get("id") is None:
            # The refund may be queued already, and a retry could refund twice.
            raise self._error("paystack POST /refund: no refund id", retryable=False)
        return str(data["id"])

    async def fetch_refund(self, refund_id: str) -> RefundInfo:
        path = f"/refund/{quote(refund_id, safe='')}"
        data = await self._call("GET", path)
        amount, currency = data.get("amount"), data.get("currency")
        reference = data.get("transaction_reference")
        if (
            type(amount) is not int
            or not isinstance(currency, str)
            or not isinstance(reference, str)
        ):
            raise self._error(f"paystack GET {path}: unexpected amount, currency or reference")
        # pending, processing and needs-attention all stay pending.
        state = _REFUND_STATES.get(str(data.get("status")), RefundState.PENDING)
        return RefundInfo(refund_id, state, amount, currency, reference)

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        signature = _header(headers, "x-paystack-signature")
        if not signature:
            raise InvalidWebhook("missing x-paystack-signature")
        expected = hmac.new(self._secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise InvalidWebhook("bad x-paystack-signature")

        try:
            payload = json.loads(body)
        except ValueError:
            raise InvalidWebhook("body is not JSON") from None
        if not isinstance(payload, dict):
            raise InvalidWebhook("body is not an event")
        event, data = payload.get("event"), payload.get("data")
        if not isinstance(event, str) or not isinstance(data, dict):
            raise InvalidWebhook("body is not an event")

        kind = _EVENT_KINDS.get(event, PaymentEventKind.OTHER)
        if event.startswith("refund."):
            # Refund events carry the refunded charge's reference, and not always a refund id.
            reference = data.get("transaction_reference")
            key = data.get("id") or data.get("refund_reference")
        else:
            reference, key = data.get("reference"), data.get("id")
        reference = reference if isinstance(reference, str) else ""
        if kind is not PaymentEventKind.OTHER and not reference:
            raise InvalidWebhook(f"{event} has no reference")

        # Paystack events have no id of their own. Redeliveries repeat the body.
        parts = [str(part) for part in (reference, key) if part]
        event_id = ":".join([event, *(parts or [hashlib.sha256(body).hexdigest()])])
        object_id = str(data["id"]) if event.startswith("refund.") and data.get("id") else None
        return PaymentEvent(event_id, kind, reference, event, object_id)

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send one request and return the response's `data`."""
        where = f"paystack {method} {path}"
        try:
            response = await self._client.request(
                method,
                self._base_url + path,
                json=body,
                headers={"Authorization": f"Bearer {self._secret_key}"},
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
        if not isinstance(payload, dict):
            raise self._error(f"{detail}, body is not JSON")
        if payload.get("status") is not True:
            raise self._error(detail, retryable=False)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise self._error(f"{detail}, no data")
        return data

    def _error(self, message: str, *, retryable: bool = True) -> ProviderError:
        return ProviderError(message.replace(self._secret_key, "***"), retryable=retryable)


def _charge_started(reference: str, data: Mapping[str, Any]) -> ChargeStarted:
    status = str(data.get("status") or "")
    if status not in _MOMO_STATUSES:
        log.warning("paystack charge %s: unexpected status %r", reference, status)
    display_text = data.get("display_text")
    return ChargeStarted(
        reference,
        _CHARGE_STATES.get(status, ChargeState.PENDING),
        display_text if isinstance(display_text, str) else None,
        needs_otp=status == "send_otp",
    )


def _local_number(phone_e164: str) -> str:
    """+233241234567 -> 0241234567, the format Paystack's Ghana examples use."""
    match = _GHANA_E164.fullmatch(phone_e164)
    if match is None:
        raise ProviderError("Ghana mobile money needs a +233 phone number", retryable=False)
    return "0" + match.group(1)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    if (value := headers.get(name)) is not None:
        return value
    return next((v for k, v in headers.items() if k.lower() == name), None)

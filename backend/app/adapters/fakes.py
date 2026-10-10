"""In-memory providers for tests and local development."""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import uuid4

from app.adapters.base import (
    ChargeStarted,
    ChargeState,
    DeliveryState,
    InvalidWebhook,
    MomoCharge,
    PaymentEvent,
    PaymentEventKind,
    ProviderError,
    RefundInfo,
    RefundState,
    SmsSent,
    VerifiedCharge,
)

FAKE_WEBHOOK_SECRET = b"fake-payment-webhook-secret"


@dataclass
class FakePaymentProvider:
    name: str = "fake"
    fee_percent: float = 1.95
    charges: dict[str, MomoCharge] = field(default_factory=dict)
    states: dict[str, ChargeState] = field(default_factory=dict)
    refunds: list[tuple[str, int]] = field(default_factory=list)
    refund_states: dict[str, RefundState] = field(default_factory=dict)
    # The next call to the named method raises the error, then it works again.
    failures: dict[str, ProviderError] = field(default_factory=dict)

    async def start_momo_charge(self, charge: MomoCharge) -> ChargeStarted:
        self._maybe_fail("start_momo_charge")
        self.charges[charge.reference] = charge
        self.states.setdefault(charge.reference, ChargeState.PENDING)
        return ChargeStarted(
            charge.reference, ChargeState.PENDING, "Approve the prompt on your phone"
        )

    async def submit_otp(self, reference: str, otp: str) -> ChargeStarted:
        self._maybe_fail("submit_otp")
        return ChargeStarted(reference, self.states.get(reference, ChargeState.PENDING))

    async def verify_charge(self, reference: str) -> VerifiedCharge:
        self._maybe_fail("verify_charge")
        charge = self.charges.get(reference)
        if charge is None:
            return VerifiedCharge(reference, ChargeState.FAILED, 0, 0, "GHS")
        state = self.states.get(reference, ChargeState.PENDING)
        fee = round(charge.amount_pesewas * self.fee_percent / 100)
        refunded = sum(a for ref, a in self.refunds if ref == reference) >= charge.amount_pesewas
        return VerifiedCharge(reference, state, charge.amount_pesewas, fee, "GHS", refunded)

    async def refund(self, reference: str, amount_pesewas: int) -> str:
        self._maybe_fail("refund")
        already = sum(a for ref, a in self.refunds if ref == reference)
        if already + amount_pesewas > self.charges[reference].amount_pesewas:
            raise ProviderError("refund exceeds the charge", retryable=False)
        self.refunds.append((reference, amount_pesewas))
        refund_id = f"refund-{len(self.refunds)}"
        self.refund_states[refund_id] = RefundState.PENDING
        return refund_id

    async def fetch_refund(self, refund_id: str) -> RefundInfo:
        self._maybe_fail("fetch_refund")
        reference, amount = self.refunds[int(refund_id.removeprefix("refund-")) - 1]
        return RefundInfo(refund_id, self.refund_states[refund_id], amount, "GHS", reference)

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        expected = hmac.new(FAKE_WEBHOOK_SECRET, body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(headers.get("x-fake-signature", "").encode(), expected.encode()):
            raise InvalidWebhook("bad signature")
        data = json.loads(body)
        return PaymentEvent(
            data["id"],
            PaymentEventKind(data["kind"]),
            data["reference"],
            data["kind"],
            data.get("object_id"),
        )

    # Test helpers ------------------------------------------------------------

    def settle(self, reference: str, state: ChargeState = ChargeState.SUCCEEDED) -> None:
        self.states[reference] = state

    def refund_outcome(self, refund_id: str, state: RefundState) -> tuple[bytes, dict[str, str]]:
        """Settle a refund and build the webhook announcing it."""
        self.refund_states[refund_id] = state
        reference = self.refunds[int(refund_id.removeprefix("refund-")) - 1][0]
        kind = (
            PaymentEventKind.REFUND_PROCESSED
            if state is RefundState.SUCCEEDED
            else PaymentEventKind.REFUND_FAILED
        )
        return self.webhook(kind, reference, object_id=refund_id)

    @staticmethod
    def webhook(
        kind: PaymentEventKind, reference: str, object_id: str | None = None
    ) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(
            {"id": str(uuid4()), "kind": kind, "reference": reference, "object_id": object_id}
        ).encode()
        signature = hmac.new(FAKE_WEBHOOK_SECRET, body, hashlib.sha512).hexdigest()
        return body, {"x-fake-signature": signature}

    def _maybe_fail(self, method: str) -> None:
        if error := self.failures.pop(method, None):
            raise error


@dataclass
class FakeSmsGateway:
    name: str = "fake"
    outbox: list[tuple[str, str]] = field(default_factory=list)
    states: dict[str, DeliveryState] = field(default_factory=dict)
    fail_next_send: bool = False

    async def send(self, to_e164: str, text: str) -> SmsSent:
        if self.fail_next_send:
            self.fail_next_send = False
            raise ProviderError("fake SMS outage")
        self.outbox.append((to_e164, text))
        message_id = f"sms-{len(self.outbox)}"
        self.states[message_id] = DeliveryState.QUEUED
        return SmsSent(message_id)

    async def delivery_state(self, provider_message_id: str) -> DeliveryState:
        return self.states.get(provider_message_id, DeliveryState.UNKNOWN)

    def messages_to(self, phone: str) -> list[str]:
        return [text for to, text in self.outbox if to == phone]

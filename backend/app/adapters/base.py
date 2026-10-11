"""Interfaces the Engine uses to reach payment, SMS, storage and classifier services.

Domain code depends only on these types. Each provider adapter translates
them to and from its own API, so providers can be swapped or faked in tests.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ProviderError(Exception):
    """The provider rejected the request or could not be reached. Safe to retry
    unless `retryable` is False."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class InvalidWebhook(Exception):
    """Signature missing or wrong, or the body is not a webhook we understand."""


# Payments --------------------------------------------------------------------


class Network(StrEnum):
    MTN = "mtn"
    TELECEL = "telecel"
    AIRTELTIGO = "airteltigo"


class ChargeState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class MomoCharge:
    reference: str
    amount_pesewas: int
    phone_e164: str
    network: Network
    # Providers that insist on an email get a stable placeholder per payer.
    email: str


@dataclass(frozen=True)
class ChargeStarted:
    reference: str
    state: ChargeState
    # What to tell the payer next, e.g. "Approve the prompt on your phone".
    display_text: str | None = None
    # Some networks need a code from the payer before the charge proceeds.
    needs_otp: bool = False


@dataclass(frozen=True)
class VerifiedCharge:
    reference: str
    state: ChargeState
    amount_pesewas: int
    fee_pesewas: int
    currency: str
    # The charge succeeded and has since been refunded in full.
    refunded: bool = False


class PaymentEventKind(StrEnum):
    CHARGE_SUCCEEDED = "charge_succeeded"
    CHARGE_FAILED = "charge_failed"
    REFUND_PROCESSED = "refund_processed"
    REFUND_FAILED = "refund_failed"
    OTHER = "other"


@dataclass(frozen=True)
class PaymentEvent:
    # Unique per delivery attempt's underlying event, used to drop duplicates.
    event_id: str
    kind: PaymentEventKind
    # Our charge reference. For refunds, the reference of the refunded charge.
    reference: str
    provider_type: str
    # The provider's own id for the object, e.g. the refund id on refund events.
    provider_object_id: str | None = None


class RefundState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RefundInfo:
    refund_id: str
    state: RefundState
    amount_pesewas: int
    currency: str
    # The refunded charge's reference.
    reference: str


class PaymentProvider(Protocol):
    name: str

    async def start_momo_charge(self, charge: MomoCharge) -> ChargeStarted: ...

    async def submit_otp(self, reference: str, otp: str) -> ChargeStarted: ...

    async def verify_charge(self, reference: str) -> VerifiedCharge:
        """Ask the provider for the charge's real state. Never trust a webhook
        body alone for money."""
        ...

    async def refund(self, reference: str, amount_pesewas: int) -> str:
        """Start a refund of a successful charge. Returns the provider's refund id."""
        ...

    async def fetch_refund(self, refund_id: str) -> RefundInfo:
        """The provider's own record of a refund, to confirm a refund webhook."""
        ...

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        """Check the signature over the raw body, then parse it. Raises InvalidWebhook."""
        ...


# SMS -------------------------------------------------------------------------


class DeliveryState(StrEnum):
    QUEUED = "queued"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SmsSent:
    provider_message_id: str


class SmsGateway(Protocol):
    name: str

    async def send(self, to_e164: str, text: str) -> SmsSent: ...

    async def delivery_state(self, provider_message_id: str) -> DeliveryState: ...


# Photos ------------------------------------------------------------------------


class PhotoStore(Protocol):
    """Private storage for pickup photos. Nothing in it is publicly readable."""

    name: str

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def signed_url(self, key: str, ttl_s: int) -> str:
        """A short-lived URL the classifier can fetch the photo from."""
        ...

    async def delete(self, key: str) -> None: ...


# Verification ------------------------------------------------------------------


class Verdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    UNCERTAIN = "uncertain"


class VerdictReason(StrEnum):
    OK = "ok"
    CONTAMINATED = "contaminated"
    WRONG_TYPE = "wrong_type"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class VerifyRequest:
    """What crosses the learned-component boundary: no identity, phone,
    location or payment data."""

    verification_id: UUID
    pickup_id: UUID
    declared_type: str
    photo_url: str
    photo_sha256: str
    captured_at: datetime
    capture_source: str


@dataclass(frozen=True)
class Classification:
    verification_id: UUID
    verdict: Verdict
    confidence: float
    reason: VerdictReason
    model_version: str
    latency_ms: int


class PhotoUnreadable(Exception):
    """The classifier can't use the photo: take another."""


class ClassifierUnavailable(Exception):
    """No usable answer: down, timed out, or replied with something invalid.
    The pickup goes to a person, never to an automatic accept or reject."""


class Classifier(Protocol):
    name: str

    async def verify(self, request: VerifyRequest) -> Classification: ...

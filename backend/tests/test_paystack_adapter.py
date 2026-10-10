import hashlib
import hmac
import json
from collections.abc import Callable

import httpx
import pytest

from app.adapters.base import (
    ChargeState,
    InvalidWebhook,
    MomoCharge,
    Network,
    PaymentEventKind,
    ProviderError,
    RefundState,
)
from app.adapters.paystack import PaystackProvider

pytestmark = pytest.mark.anyio

SECRET = "paystack-dummy-secret-for-tests"
Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


def paystack(reply: Reply) -> tuple[PaystackProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return reply(request) if callable(reply) else reply

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PaystackProvider(SECRET, client=client), requests


def ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"status": True, "message": "Charge attempted", "data": data})


def charge(network: Network = Network.MTN) -> MomoCharge:
    return MomoCharge("pay-123", 2500, "+233241234567", network, "u1@payers.imfucuza.app")


def signed(event: dict, key: str = SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(event).encode()
    return body, {"x-paystack-signature": hmac.new(key.encode(), body, hashlib.sha512).hexdigest()}


# Charges ---------------------------------------------------------------------


async def test_momo_charge_posts_a_ghs_mobile_money_charge_with_our_reference():
    provider, requests = paystack(ok({"reference": "pay-123", "status": "pay_offline"}))

    await provider.start_momo_charge(charge())

    [request] = requests
    assert request.method == "POST"
    assert request.url == "https://api.paystack.co/charge"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {
        "email": "u1@payers.imfucuza.app",
        "amount": 2500,
        "currency": "GHS",
        "reference": "pay-123",
        "mobile_money": {"phone": "0241234567", "provider": "mtn"},
    }
    assert request.extensions["timeout"]["read"] == 15.0


@pytest.mark.parametrize(
    ("network", "code"),
    [(Network.MTN, "mtn"), (Network.TELECEL, "vod"), (Network.AIRTELTIGO, "atl")],
)
async def test_momo_charge_sends_paystacks_provider_code_for_the_network(network, code):
    provider, requests = paystack(ok({"reference": "pay-123", "status": "pay_offline"}))

    await provider.start_momo_charge(charge(network))

    assert json.loads(requests[0].content)["mobile_money"]["provider"] == code


@pytest.mark.parametrize(
    ("status", "state", "needs_otp"),
    [
        ("pay_offline", ChargeState.PENDING, False),
        ("pending", ChargeState.PENDING, False),
        ("send_otp", ChargeState.PENDING, True),
        ("success", ChargeState.SUCCEEDED, False),
        ("failed", ChargeState.FAILED, False),
        ("timeout", ChargeState.PENDING, False),
        ("send_pin", ChargeState.PENDING, False),
        ("something_new", ChargeState.PENDING, False),
    ],
)
async def test_momo_charge_maps_the_charge_status(status, state, needs_otp):
    provider, _ = paystack(ok({"reference": "pay-123", "status": status}))

    started = await provider.start_momo_charge(charge())

    assert (started.reference, started.state, started.needs_otp) == ("pay-123", state, needs_otp)


async def test_momo_charge_passes_on_paystacks_display_text():
    text = "Please complete authorization process on your mobile phone"
    provider, _ = paystack(
        ok({"reference": "pay-123", "status": "pay_offline", "display_text": text})
    )

    started = await provider.start_momo_charge(charge())

    assert started.display_text == text


async def test_momo_charge_without_display_text_has_none():
    provider, _ = paystack(ok({"reference": "pay-123", "status": "pending"}))

    assert (await provider.start_momo_charge(charge())).display_text is None


async def test_momo_charge_refuses_a_non_ghana_number_without_calling_paystack():
    provider, requests = paystack(ok({}))
    foreign = MomoCharge("pay-123", 2500, "+2348031234567", Network.MTN, "u1@payers.imfucuza.app")

    with pytest.raises(ProviderError) as raised:
        await provider.start_momo_charge(foreign)

    assert raised.value.retryable is False
    assert requests == []


async def test_submit_otp_posts_the_code_for_the_charge():
    provider, requests = paystack(ok({"reference": "pay-123", "status": "pay_offline"}))

    started = await provider.submit_otp("pay-123", "482913")

    [request] = requests
    assert request.method == "POST"
    assert request.url == "https://api.paystack.co/charge/submit_otp"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {"otp": "482913", "reference": "pay-123"}
    assert (started.state, started.needs_otp) == (ChargeState.PENDING, False)


async def test_submit_otp_maps_a_successful_charge():
    provider, _ = paystack(ok({"reference": "pay-123", "status": "success"}))

    assert (await provider.submit_otp("pay-123", "482913")).state is ChargeState.SUCCEEDED


# Verify ----------------------------------------------------------------------


def transaction(status: str = "success", fees: int | None = 49) -> dict:
    return {
        "reference": "pay-123",
        "status": status,
        "amount": 2500,
        "fees": fees,
        "currency": "GHS",
    }


async def test_verify_gets_the_transaction_by_reference():
    provider, requests = paystack(ok(transaction()))

    verified = await provider.verify_charge("pay-123")

    [request] = requests
    assert request.method == "GET"
    assert request.url == "https://api.paystack.co/transaction/verify/pay-123"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert verified.reference == "pay-123"
    assert (verified.amount_pesewas, verified.fee_pesewas, verified.currency) == (2500, 49, "GHS")


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("success", ChargeState.SUCCEEDED),
        ("failed", ChargeState.FAILED),
        ("reversed", ChargeState.FAILED),
        ("abandoned", ChargeState.PENDING),
        ("ongoing", ChargeState.PENDING),
        ("pending", ChargeState.PENDING),
        ("processing", ChargeState.PENDING),
        ("queued", ChargeState.PENDING),
        ("something_new", ChargeState.PENDING),
    ],
)
async def test_verify_maps_the_transaction_status(status, state):
    provider, _ = paystack(ok(transaction(status)))

    assert (await provider.verify_charge("pay-123")).state is state


async def test_verify_counts_missing_fees_as_zero():
    provider, _ = paystack(ok(transaction("failed", fees=None)))

    assert (await provider.verify_charge("pay-123")).fee_pesewas == 0


async def test_verify_rejects_a_transaction_without_an_integer_amount():
    provider, _ = paystack(ok(transaction() | {"amount": "2500"}))

    with pytest.raises(ProviderError):
        await provider.verify_charge("pay-123")


# Refunds ---------------------------------------------------------------------


async def test_refund_posts_the_charge_reference_and_amount_and_returns_the_refund_id():
    provider, requests = paystack(ok({"id": 3018284, "status": "pending"}))

    refund_id = await provider.refund("pay-123", 2500)

    [request] = requests
    assert request.method == "POST"
    assert request.url == "https://api.paystack.co/refund"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {"transaction": "pay-123", "amount": 2500}
    assert refund_id == "3018284"


async def test_refund_without_an_id_is_not_retried():
    provider, _ = paystack(ok({"status": "pending"}))

    with pytest.raises(ProviderError) as raised:
        await provider.refund("pay-123", 2500)

    assert raised.value.retryable is False


# Errors ----------------------------------------------------------------------

CALLS = {
    "charge": lambda p: p.start_momo_charge(charge()),
    "otp": lambda p: p.submit_otp("pay-123", "482913"),
    "verify": lambda p: p.verify_charge("pay-123"),
    "refund": lambda p: p.refund("pay-123", 2500),
}


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot connect, Bearer {SECRET}", request=request)


def refusal(code: int, message: str) -> Reply:
    return lambda _: httpx.Response(code, json={"status": False, "message": message})


ERRORS = {
    "validation_400": (refusal(400, "Invalid"), False),
    "unauthorized_401": (refusal(401, "Bad key"), False),
    "rate_limited_429": (refusal(429, "Slow down"), True),
    "server_500": (refusal(500, "Oops"), True),
    "html_502": (lambda _: httpx.Response(502, text="<html>Bad gateway</html>"), True),
    "status_false_200": (refusal(200, "Declined"), False),
    "timeout": (timeout, True),
    "unreachable": (unreachable, True),
}


@pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
@pytest.mark.parametrize(("reply", "retryable"), ERRORS.values(), ids=ERRORS.keys())
async def test_failures_raise_provider_error_without_the_secret_key(call, reply, retryable):
    provider, _ = paystack(reply)

    with pytest.raises(ProviderError) as raised:
        await call(provider)

    assert raised.value.retryable is retryable
    assert SECRET not in str(raised.value)


async def test_error_message_includes_paystacks_reason():
    provider, _ = paystack(httpx.Response(400, json={"status": False, "message": "Invalid phone"}))

    with pytest.raises(ProviderError, match="HTTP 400 Invalid phone"):
        await provider.verify_charge("pay-123")


def test_an_empty_secret_key_is_refused():
    with pytest.raises(ValueError, match="secret key"):
        PaystackProvider("")


# Webhooks --------------------------------------------------------------------

CHARGE_SUCCESS = {
    "event": "charge.success",
    "data": {"id": 59214, "status": "success", "reference": "pay-123", "amount": 2500},
}
REFUND_PROCESSED = {
    "event": "refund.processed",
    "data": {
        "status": "processed",
        "transaction_reference": "pay-123",
        "refund_reference": "132013318360",
        "amount": "2500",
    },
}


def test_webhook_maps_charge_success():
    provider, _ = paystack(ok({}))

    event = provider.parse_webhook(*signed(CHARGE_SUCCESS))

    assert event.kind is PaymentEventKind.CHARGE_SUCCEEDED
    assert event.reference == "pay-123"
    assert event.provider_type == "charge.success"
    assert event.event_id == "charge.success:pay-123:59214"


def test_webhook_maps_refund_processed_to_the_refunded_charge():
    provider, _ = paystack(ok({}))

    event = provider.parse_webhook(*signed(REFUND_PROCESSED))

    assert event.kind is PaymentEventKind.REFUND_PROCESSED
    assert event.reference == "pay-123"
    assert event.event_id == "refund.processed:pay-123:132013318360"


def test_webhook_maps_refund_failed():
    provider, _ = paystack(ok({}))
    failed = {"event": "refund.failed", "data": REFUND_PROCESSED["data"] | {"status": "failed"}}

    event = provider.parse_webhook(*signed(failed))

    assert (event.kind, event.reference) == (PaymentEventKind.REFUND_FAILED, "pay-123")


@pytest.mark.parametrize("name", ["refund.pending", "refund.processing", "refund.needs-attention"])
def test_webhook_treats_refunds_in_flight_as_other(name):
    provider, _ = paystack(ok({}))

    event = provider.parse_webhook(*signed({"event": name, "data": REFUND_PROCESSED["data"]}))

    assert (event.kind, event.reference, event.provider_type) == (
        PaymentEventKind.OTHER,
        "pay-123",
        name,
    )


def test_webhook_treats_unknown_events_as_other():
    provider, _ = paystack(ok({}))

    event = provider.parse_webhook(*signed({"event": "transfer.success", "data": {"id": 7}}))

    assert (event.kind, event.reference, event.event_id) == (
        PaymentEventKind.OTHER,
        "",
        "transfer.success:7",
    )


def test_webhook_event_id_is_stable_across_redeliveries():
    provider, _ = paystack(ok({}))
    bare = {"event": "subscription.create", "data": {"plan": {}}}

    first, again = provider.parse_webhook(*signed(bare)), provider.parse_webhook(*signed(bare))
    other = provider.parse_webhook(*signed({"event": "subscription.create", "data": {"x": 1}}))

    assert first.event_id == again.event_id
    assert first.event_id != other.event_id


def test_webhook_accepts_the_signature_header_in_any_case():
    provider, _ = paystack(ok({}))
    body, headers = signed(CHARGE_SUCCESS)

    event = provider.parse_webhook(body, {"X-Paystack-Signature": headers["x-paystack-signature"]})

    assert event.kind is PaymentEventKind.CHARGE_SUCCEEDED


def test_webhook_without_a_signature_is_invalid():
    provider, _ = paystack(ok({}))
    body, _ = signed(CHARGE_SUCCESS)

    with pytest.raises(InvalidWebhook):
        provider.parse_webhook(body, {})


def test_webhook_signed_with_another_key_is_invalid():
    provider, _ = paystack(ok({}))
    body, headers = signed(CHARGE_SUCCESS, key="sk_test_someone_else")

    with pytest.raises(InvalidWebhook):
        provider.parse_webhook(body, headers)


def test_webhook_with_a_tampered_body_is_invalid():
    provider, _ = paystack(ok({}))
    body, headers = signed(CHARGE_SUCCESS)
    tampered = body.replace(b"pay-123", b"pay-999")

    with pytest.raises(InvalidWebhook):
        provider.parse_webhook(tampered, headers)


def test_webhook_with_a_non_ascii_signature_is_invalid():
    provider, _ = paystack(ok({}))
    body, _ = signed(CHARGE_SUCCESS)

    with pytest.raises(InvalidWebhook):
        provider.parse_webhook(body, {"x-paystack-signature": "é" * 128})


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[1, 2]", b'{"event": "charge.success"}', b'{"data": {}}'],
    ids=["not_json", "not_an_object", "no_data", "no_event"],
)
def test_webhook_with_a_signed_but_unreadable_body_is_invalid(body):
    provider, _ = paystack(ok({}))
    signature = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()

    with pytest.raises(InvalidWebhook):
        provider.parse_webhook(body, {"x-paystack-signature": signature})


@pytest.mark.parametrize(
    "event",
    [
        {"event": "charge.success", "data": {"id": 59214, "status": "success"}},
        {"event": "refund.processed", "data": {"status": "processed", "refund_reference": "1"}},
    ],
    ids=["charge", "refund"],
)
def test_webhook_for_money_without_a_reference_is_invalid(event):
    provider, _ = paystack(ok({}))
    body, headers = signed(event)

    with pytest.raises(InvalidWebhook):
        provider.parse_webhook(body, headers)


# Refund lookups and lost charges -----------------------------------------------


async def test_verify_of_a_reference_paystack_never_saw_is_a_failed_charge():
    provider, _ = paystack(
        httpx.Response(400, json={"status": False, "message": "Transaction reference not found"})
    )

    verified = await provider.verify_charge("pay-123")

    assert verified.state is ChargeState.FAILED


async def test_verify_still_raises_other_refusals():
    provider, _ = paystack(httpx.Response(401, json={"status": False, "message": "Invalid key"}))

    with pytest.raises(ProviderError):
        await provider.verify_charge("pay-123")


@pytest.mark.parametrize(("status", "refunded"), [("reversed", True), ("success", False)])
async def test_verify_says_whether_the_charge_was_refunded(status, refunded):
    provider, _ = paystack(ok(transaction(status)))

    assert (await provider.verify_charge("pay-123")).refunded is refunded


def refund_record(status: str = "processed") -> dict:
    return {
        "id": 3018284,
        "status": status,
        "amount": 2500,
        "currency": "GHS",
        "transaction_reference": "pay-123",
    }


async def test_fetch_refund_gets_the_refund_by_id():
    provider, requests = paystack(ok(refund_record()))

    info = await provider.fetch_refund("3018284")

    [request] = requests
    assert (request.method, str(request.url)) == ("GET", "https://api.paystack.co/refund/3018284")
    assert (info.reference, info.amount_pesewas, info.currency) == ("pay-123", 2500, "GHS")


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("processed", RefundState.SUCCEEDED),
        ("failed", RefundState.FAILED),
        ("pending", RefundState.PENDING),
        ("processing", RefundState.PENDING),
        ("needs-attention", RefundState.PENDING),
    ],
)
async def test_fetch_refund_maps_the_refund_status(status, state):
    provider, _ = paystack(ok(refund_record(status)))

    assert (await provider.fetch_refund("3018284")).state is state


async def test_fetch_refund_rejects_a_record_without_its_charge_reference():
    record = refund_record()
    del record["transaction_reference"]
    provider, _ = paystack(ok(record))

    with pytest.raises(ProviderError):
        await provider.fetch_refund("3018284")


def test_refund_webhook_carries_the_refund_id_when_paystack_sends_one():
    provider, _ = paystack(ok({}))
    with_id = {**REFUND_PROCESSED, "data": {**REFUND_PROCESSED["data"], "id": 3018284}}

    assert provider.parse_webhook(*signed(with_id)).provider_object_id == "3018284"
    # Paystack's sample event has no id. The Engine then uses the id it stored.
    assert provider.parse_webhook(*signed(REFUND_PROCESSED)).provider_object_id is None

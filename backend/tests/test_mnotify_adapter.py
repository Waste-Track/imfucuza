import json
import logging
import traceback
from collections.abc import Callable

import httpx
import pytest

from app.adapters.base import DeliveryState, ProviderError
from app.adapters.mnotify import MnotifyGateway

pytestmark = pytest.mark.anyio

KEY = "mnotify-dummy-key-for-tests"
CAMPAIGN = "A59CCB70-662D-45EF-9976-1EFAD249793D"
Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


def mnotify(reply: Reply) -> tuple[MnotifyGateway, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return reply(request) if callable(reply) else reply

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MnotifyGateway(KEY, sender_id="Imfucuza", client=client), requests


def sent(campaign_id: str | None = CAMPAIGN) -> httpx.Response:
    summary = {"type": "API QUICK SMS", "total_sent": 1, "numbers_sent": ["0241234567"]}
    if campaign_id is not None:
        summary["_id"] = campaign_id
    body = {"status": "success", "code": "2000", "message": "messages sent successfully"}
    return httpx.Response(200, json=body | {"summary": summary})


def report(*statuses: str) -> httpx.Response:
    entries = [
        {"_id": 60711577 + i, "recipient": "233241234567", "status": status, "retries": 0}
        for i, status in enumerate(statuses)
    ]
    return httpx.Response(200, json={"status": "success", "report": entries})


# Sending ---------------------------------------------------------------------


async def test_send_posts_a_quick_sms_from_our_sender_id():
    gateway, requests = mnotify(sent())

    await gateway.send("+233241234567", "Your pickup PIN is 4821")

    [request] = requests
    assert request.method == "POST"
    assert request.url.copy_with(query=None) == "https://api.mnotify.com/api/sms/quick"
    assert dict(request.url.params) == {"key": KEY}
    assert json.loads(request.content) == {
        "recipient": ["0241234567"],
        "sender": "Imfucuza",
        "message": "Your pickup PIN is 4821",
        "is_schedule": False,
        "schedule_date": "",
    }
    assert request.extensions["timeout"]["read"] == 15.0


async def test_send_returns_the_campaign_id_for_status_lookups():
    gateway, _ = mnotify(sent())

    assert (await gateway.send("+233241234567", "Hello")).provider_message_id == CAMPAIGN


async def test_send_without_a_campaign_id_is_not_retried():
    gateway, _ = mnotify(sent(campaign_id=None))

    with pytest.raises(ProviderError) as raised:
        await gateway.send("+233241234567", "Hello")

    assert raised.value.retryable is False


async def test_send_refuses_a_non_ghana_number_without_calling_mnotify():
    gateway, requests = mnotify(sent())

    with pytest.raises(ProviderError) as raised:
        await gateway.send("+2348031234567", "Hello")

    assert raised.value.retryable is False
    assert requests == []


def test_sender_id_longer_than_eleven_characters_is_refused():
    with pytest.raises(ValueError, match="sender id"):
        MnotifyGateway(KEY, sender_id="ImfucuzaWaste")


def test_an_empty_api_key_is_refused():
    with pytest.raises(ValueError, match="API key"):
        MnotifyGateway("", sender_id="Imfucuza")


# Delivery status -------------------------------------------------------------


async def test_delivery_state_gets_the_campaign_report():
    gateway, requests = mnotify(report("DELIVERED"))

    await gateway.delivery_state(CAMPAIGN)

    [request] = requests
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == f"https://api.mnotify.com/api/campaign/{CAMPAIGN}"
    assert dict(request.url.params) == {"key": KEY}


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("DELIVERED", DeliveryState.DELIVERED),
        ("SUBMITTED", DeliveryState.QUEUED),
        ("UNDELIVERED", DeliveryState.FAILED),
        ("FAILED", DeliveryState.FAILED),
        ("REJECTED", DeliveryState.FAILED),
        ("delivered", DeliveryState.DELIVERED),
        ("SOMETHING_NEW", DeliveryState.UNKNOWN),
    ],
)
async def test_delivery_state_maps_the_report_status(status, state):
    gateway, _ = mnotify(report(status))

    assert await gateway.delivery_state(CAMPAIGN) is state


async def test_delivery_state_with_an_empty_report_is_unknown():
    gateway, _ = mnotify(report())

    assert await gateway.delivery_state(CAMPAIGN) is DeliveryState.UNKNOWN


# Errors ----------------------------------------------------------------------

CALLS = {
    "send": lambda g: g.send("+233241234567", "Hello"),
    "delivery_state": lambda g: g.delivery_state(CAMPAIGN),
}


def refusal(code: int, message: str) -> Reply:
    return lambda _: httpx.Response(code, json={"status": "error", "message": message})


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout(f"timed out reading {request.url}", request=request)


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)


ERRORS = {
    "validation_422": (refusal(422, "Invalid sender"), False),
    "unauthorized_401": (refusal(401, f"Invalid key {KEY}"), False),
    "rate_limited_429": (refusal(429, "Slow down"), True),
    "server_500": (refusal(500, "Oops"), True),
    "html_502": (lambda _: httpx.Response(502, text="<html>Bad gateway</html>"), True),
    "status_error_200": (refusal(200, "Insufficient balance"), False),
    "not_json_200": (lambda _: httpx.Response(200, text="OK"), False),
    "timeout": (timeout, True),
    "unreachable": (unreachable, True),
}


@pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
@pytest.mark.parametrize(("reply", "retryable"), ERRORS.values(), ids=ERRORS.keys())
async def test_failures_raise_provider_error_without_the_api_key(call, reply, retryable):
    gateway, _ = mnotify(reply)

    with pytest.raises(ProviderError) as raised:
        await call(gateway)

    assert raised.value.retryable is retryable
    assert KEY not in "".join(traceback.format_exception(raised.value))


async def test_failures_hide_an_api_key_that_the_url_percent_encodes():
    key = "k/ey+1=="
    client = httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
    gateway = MnotifyGateway(key, sender_id="Imfucuza", client=client)

    with pytest.raises(ProviderError) as raised:
        await gateway.delivery_state(CAMPAIGN)

    assert "key=***" in str(raised.value)
    assert "k%2Fey" not in str(raised.value)


async def test_error_message_includes_mnotifys_reason():
    gateway, _ = mnotify(refusal(422, "Invalid sender"))

    with pytest.raises(ProviderError, match="HTTP 422 Invalid sender"):
        await gateway.send("+233241234567", "Hello")


async def test_httpx_request_log_hides_the_api_key(caplog):
    gateway, _ = mnotify(sent())
    caplog.set_level(logging.INFO, logger="httpx")

    await gateway.send("+233241234567", "Hello")

    lines = [record.getMessage() for record in caplog.records if record.name == "httpx"]
    assert lines
    assert all("key=***" in line and KEY not in line for line in lines)

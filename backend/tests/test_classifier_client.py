import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import anyio
import httpx
import pytest

from app.adapters.base import (
    ClassifierUnavailable,
    PhotoUnreadable,
    Verdict,
    VerdictReason,
    VerifyRequest,
)
from app.adapters.classifier import FAILURES_TO_OPEN, OPEN_FOR_S, HttpClassifier, NoClassifier

pytestmark = pytest.mark.anyio

TOKEN = "classifier-dummy-token"
Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def classifier(
    *replies: Reply, timeout_s: float = 8.0
) -> tuple[HttpClassifier, list[httpx.Request], Clock]:
    requests: list[httpx.Request] = []
    queue = list(replies)
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return reply(request) if callable(reply) else reply

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        HttpClassifier(
            "https://cls.example/",
            TOKEN,
            client=client,
            clock=clock,
            sleep=clock.sleep,
            timeout_s=timeout_s,
        ),
        requests,
        clock,
    )


def verify_request() -> VerifyRequest:
    return VerifyRequest(
        verification_id=uuid4(),
        pickup_id=uuid4(),
        declared_type="pet",
        photo_url="https://photos.test/p.jpg?token=x",
        photo_sha256="ab" * 32,
        captured_at=datetime(2026, 10, 11, 9, 30, tzinfo=UTC),
        capture_source="rider",
    )


def answer(**overrides) -> Callable[[httpx.Request], httpx.Response]:
    def reply(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "verification_id": sent["verification_id"],
                "decision": "accept",
                "confidence": 0.93,
                "reason": "ok",
                "model_version": "cls-2026.11.0",
                **overrides,
            },
        )

    return reply


async def test_sends_the_contract_request_and_reads_the_verdict():
    client, requests, _ = classifier(answer())
    request = verify_request()

    result = await client.verify(request)

    [sent] = requests
    assert str(sent.url) == "https://cls.example/v1/verify"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["idempotency-key"] == str(request.verification_id)
    assert json.loads(sent.content) == {
        "verification_id": str(request.verification_id),
        "request_id": str(request.pickup_id),
        "declared_type": "pet",
        "photo": {"url": request.photo_url, "sha256": request.photo_sha256},
        "captured_at": "2026-10-11T09:30:00+00:00",
        "capture_source": "rider",
    }
    assert (result.verdict, result.confidence, result.reason) == (
        Verdict.ACCEPT,
        0.93,
        VerdictReason.OK,
    )
    assert result.model_version == "cls-2026.11.0"


async def test_unreadable_photo_asks_for_a_retake():
    client, requests, _ = classifier(httpx.Response(422, json={"detail": "blurry"}))

    with pytest.raises(PhotoUnreadable):
        await client.verify(verify_request())

    assert len(requests) == 1


@pytest.mark.parametrize(
    "first_failure",
    [httpx.Response(503), httpx.Response(429), httpx.ReadTimeout("slow")],
    ids=["503", "429", "timeout"],
)
async def test_one_retry_with_the_same_id_after_a_pause(first_failure):
    def fail(request: httpx.Request) -> httpx.Response:
        if isinstance(first_failure, Exception):
            raise first_failure
        return first_failure

    client, requests, clock = classifier(fail, answer())

    result = await client.verify(verify_request())

    assert result.verdict is Verdict.ACCEPT
    assert len(requests) == 2
    assert requests[0].headers["idempotency-key"] == requests[1].headers["idempotency-key"]
    assert clock.slept == [0.5]


async def test_two_failures_mean_unavailable():
    client, requests, _ = classifier(httpx.Response(500))

    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())

    assert len(requests) == 2


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_client_errors_are_not_retried(status):
    client, requests, _ = classifier(httpx.Response(status))

    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())

    assert len(requests) == 1


@pytest.mark.parametrize(
    "bad_reply",
    [
        answer(decision="maybe"),
        answer(confidence=1.5),
        answer(confidence="NaN"),
        answer(confidence=True),
        answer(confidence="0.95"),
        answer(reason="looks fine"),
        answer(model_version=""),
        answer(verification_id=str(uuid4())),
        lambda request: httpx.Response(200, content=b"<html>proxy error</html>"),
    ],
    ids=[
        "verdict",
        "confidence",
        "nan",
        "bool",
        "string",
        "reason",
        "model",
        "other-id",
        "not-json",
    ],
)
async def test_a_reply_outside_the_contract_is_never_trusted(bad_reply):
    client, _, _ = classifier(bad_reply)

    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())


async def test_repeated_failures_stop_calls_for_a_minute_then_try_again():
    # Each failed verify makes two calls: the first try and its retry.
    client, requests, clock = classifier(*[httpx.Response(500)] * 2 * FAILURES_TO_OPEN, answer())
    for _ in range(FAILURES_TO_OPEN):
        with pytest.raises(ClassifierUnavailable):
            await client.verify(verify_request())
    calls = len(requests)

    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())
    skipped = len(requests) == calls

    clock.now += OPEN_FOR_S
    recovered = await client.verify(verify_request())

    assert skipped
    assert recovered.verdict is Verdict.ACCEPT


async def test_an_unreadable_photo_does_not_count_as_a_failure():
    client, requests, _ = classifier(httpx.Response(422))
    for _ in range(FAILURES_TO_OPEN + 1):
        with pytest.raises(PhotoUnreadable):
            await client.verify(verify_request())

    assert len(requests) == FAILURES_TO_OPEN + 1


async def test_no_classifier_sends_everything_to_a_person():
    with pytest.raises(ClassifierUnavailable):
        await NoClassifier().verify(verify_request())


async def test_a_reply_trickling_in_is_cut_off_at_the_timeout():
    async def slow(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(1)
        return answer()(request)

    client, requests, _ = classifier(slow, timeout_s=0.05)
    started = time.monotonic()

    with pytest.raises(ClassifierUnavailable, match="timed out"):
        await client.verify(verify_request())

    assert len(requests) == 2
    assert time.monotonic() - started < 0.5


async def test_an_unexpected_error_still_means_unavailable():
    def broken(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("client closed")

    client, _, _ = classifier(broken)

    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())


async def test_a_working_reply_ends_a_run_of_failures():
    client, requests, _ = classifier(
        *[httpx.Response(500)] * 4, httpx.Response(422), *[httpx.Response(500)] * 2, answer()
    )
    for expected in (ClassifierUnavailable, ClassifierUnavailable, PhotoUnreadable):
        with pytest.raises(expected):
            await client.verify(verify_request())
    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())

    assert (await client.verify(verify_request())).verdict is Verdict.ACCEPT
    assert len(requests) == 8


async def test_after_the_pause_one_more_failure_pauses_again():
    client, requests, clock = classifier(httpx.Response(500))
    for _ in range(FAILURES_TO_OPEN):
        with pytest.raises(ClassifierUnavailable):
            await client.verify(verify_request())
    clock.now += OPEN_FOR_S

    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())
    calls = len(requests)
    with pytest.raises(ClassifierUnavailable):
        await client.verify(verify_request())

    assert len(requests) == calls

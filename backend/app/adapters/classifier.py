"""The waste classifier, called over HTTP as set out in spec/classifier-contract.md.

Any doubt about a reply raises ClassifierUnavailable, which sends the pickup
to a person: the Engine never accepts or rejects on an answer it can't trust.
"""

import time
from collections.abc import Awaitable, Callable
from uuid import UUID

import anyio
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.base import (
    Classification,
    ClassifierUnavailable,
    PhotoUnreadable,
    Verdict,
    VerdictReason,
    VerifyRequest,
)

TIMEOUT_S = 8.0
RETRY_AFTER_S = 0.5
# After this many failed calls in a row, stop calling for a while.
FAILURES_TO_OPEN = 3
OPEN_FOR_S = 60.0


class _Reply(BaseModel):
    # Strict: `true` or "0.95" must not pass as a confidence.
    model_config = ConfigDict(extra="ignore", strict=True)

    verification_id: UUID
    decision: Verdict
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: VerdictReason
    model_version: str = Field(min_length=1, max_length=100)


class HttpClassifier:
    name = "http"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        if not base_url or not token:
            raise ValueError("the classifier needs a URL and a token")
        self._url = f"{base_url.rstrip('/')}/v1/verify"
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._clock = clock
        self._sleep = sleep
        self._timeout_s = timeout_s
        self._failures = 0
        self._open_until = 0.0

    async def verify(self, request: VerifyRequest) -> Classification:
        if self._clock() < self._open_until:
            raise ClassifierUnavailable("classifier: not called, it failed repeatedly just now")
        try:
            result = await self._verify(request)
        except PhotoUnreadable:
            self._failures = 0
            raise
        except Exception as exc:
            self._failures += 1
            if self._failures >= FAILURES_TO_OPEN:
                self._open_until = self._clock() + OPEN_FOR_S
            if isinstance(exc, ClassifierUnavailable):
                raise
            raise ClassifierUnavailable(f"classifier: {type(exc).__name__}") from None
        self._failures = 0
        return result

    async def _verify(self, request: VerifyRequest) -> Classification:
        body = {
            "verification_id": str(request.verification_id),
            "request_id": str(request.pickup_id),
            "declared_type": request.declared_type,
            "photo": {"url": request.photo_url, "sha256": request.photo_sha256},
            "captured_at": request.captured_at.isoformat(),
            "capture_source": request.capture_source,
        }
        headers = {
            "authorization": f"Bearer {self._token}",
            # The same id on the retry, so the classifier can tell it's one request.
            "idempotency-key": str(request.verification_id),
        }
        problem = ""
        for attempt in range(2):
            if attempt:
                await self._sleep(RETRY_AFTER_S)
            started = self._clock()
            try:
                # httpx's timeout applies to each read, not to the whole reply.
                with anyio.fail_after(self._timeout_s):
                    response = await self._client.post(
                        self._url, json=body, headers=headers, timeout=self._timeout_s
                    )
            except (TimeoutError, httpx.TimeoutException):
                problem = "timed out"
                continue
            except httpx.HTTPError as exc:
                problem = type(exc).__name__
                continue
            status = response.status_code
            if status == 422:
                raise PhotoUnreadable("the classifier can't use this photo")
            if status == 200:
                latency_ms = int((self._clock() - started) * 1000)
                return _parse(response, request.verification_id, latency_ms)
            problem = f"HTTP {status}"
            if status < 500 and status != 429:
                break
        raise ClassifierUnavailable(f"classifier: {problem}")


def _parse(response: httpx.Response, verification_id: UUID, latency_ms: int) -> Classification:
    try:
        reply = _Reply.model_validate_json(response.content)
    except ValidationError:
        raise ClassifierUnavailable("classifier: the reply doesn't match the contract") from None
    if reply.verification_id != verification_id:
        raise ClassifierUnavailable("classifier: the reply is for another verification")
    return Classification(
        verification_id=reply.verification_id,
        verdict=reply.decision,
        confidence=reply.confidence,
        reason=reply.reason,
        model_version=reply.model_version,
        latency_ms=latency_ms,
    )


class NoClassifier:
    """Until the classifier is deployed, every plastic pickup goes to a supervisor."""

    name = "none"

    async def verify(self, request: VerifyRequest) -> Classification:
        raise ClassifierUnavailable("no classifier is configured")

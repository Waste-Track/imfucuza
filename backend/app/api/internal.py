"""Routes called by Supabase Cron, never by clients."""

import asyncio
import hmac
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app import jobs, services
from app.config import Settings, get_settings

# Render's free plan puts the classifier to sleep after 15 minutes without a
# request, and waking it takes longer than a verify is allowed to wait.
WARM_CLASSIFIER_EVERY_S = 600.0

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


def require_internal_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.internal_secret.get_secret_value()
    if not expected:
        # Disabled until a secret is configured: behave as if the route doesn't exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    # Compare bytes: compare_digest raises on non-ASCII str instead of failing.
    if x_internal_secret is None or not hmac.compare_digest(
        x_internal_secret.encode(), expected.encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


@router.post("/tick", dependencies=[Depends(require_internal_secret)])
async def tick(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    state = request.app.state
    _keep_classifier_awake(state)
    # Cron calls every 10 s whether or not the last tick has finished. Ticks
    # side by side would hold every pool connection during a provider outage.
    if state.tick_lock.locked():
        return {"succeeded": 0, "retried": 0, "failed": 0}
    async with state.tick_lock:
        report = await jobs.run_due(state.pool, limit=settings.jobs_per_tick)
    state.last_tick_at = datetime.now(UTC)
    return {"succeeded": report.succeeded, "retried": report.retried, "failed": report.failed}


def _keep_classifier_awake(state) -> None:
    now = time.monotonic()
    if (
        state.classifier_warmed_at is not None
        and now - state.classifier_warmed_at < WARM_CLASSIFIER_EVERY_S
    ):
        return
    state.classifier_warmed_at = now
    # In the background: a sleeping classifier takes longer to answer than a tick.
    task = asyncio.create_task(services.get().classifier.warm())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)

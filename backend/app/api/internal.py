"""Routes called by Supabase Cron, never by clients."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app import jobs
from app.config import Settings, get_settings

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
    report = await jobs.run_due(request.app.state.pool, limit=settings.jobs_per_tick)
    return {"succeeded": report.succeeded, "retried": report.retried, "failed": report.failed}

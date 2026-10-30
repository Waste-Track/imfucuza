import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import services
from app.api import errors, households, internal, me, riders, supervisor, ussd, webhooks
from app.config import get_settings
from app.db import create_pool


class _RedactUssdToken(logging.Filter):
    """The USSD token travels in the callback path, so keep it out of access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        token = get_settings().ussd_webhook_token.get_secret_value()
        if token and isinstance(record.args, tuple):
            record.args = tuple(
                a.replace(token, "***") if isinstance(a, str) else a for a in record.args
            )
        return True


log = logging.getLogger(__name__)


class _CrashAsJson:
    """Answers an unexpected error with a 500 from inside the CORS middleware.
    Starlette's own 500 comes from outside it, without CORS headers, so a
    browser can't read it and the app could only say it was offline."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            log.exception("unhandled error: %s %s", scope["method"], scope["path"])
            if started:
                raise
            response = JSONResponse(
                status_code=500,
                content={"detail": "something went wrong on our side, try again in a minute"},
            )
            await response(scope, receive, send)


class _QuietTicks(logging.Filter):
    """Cron ticks every 10 s: log only the ticks that fail."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (
            isinstance(args, tuple)
            and len(args) == 5
            and args[2] == "/internal/tick"
            and args[4] == 200
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.check_deployable()
    access = logging.getLogger("uvicorn.access")
    for log_filter in (_RedactUssdToken, _QuietTicks):
        if not any(isinstance(f, log_filter) for f in access.filters):
            access.addFilter(log_filter())
    services.ensure(settings)
    pool = create_pool(settings)
    # Fail the deploy if the database is unreachable, rather than serving /health.
    await pool.open(wait=True, timeout=15)
    app.state.pool = pool
    app.state.tick_lock = anyio.Lock()
    # Photos are processed one at a time, off the event loop.
    app.state.photo_limiter = anyio.CapacityLimiter(1)
    app.state.last_tick_at = None
    app.state.classifier_warmed_at = None
    # Strong references, or the event loop may drop a task before it finishes.
    app.state.background_tasks = set()
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="Engine", version="0.1.0", lifespan=lifespan)
errors.install(app)
# Added first, so it runs inside CORS.
app.add_middleware(_CrashAsJson)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)
for module in (me, households, riders, supervisor, webhooks, ussd, internal):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict[str, str | None]:
    # The deploy job polls this until `commit` matches the SHA it deployed.
    # `last_tick_at` is for an uptime monitor: Cron's own history reports
    # success even when every tick is refused.
    last_tick = getattr(app.state, "last_tick_at", None)
    return {
        "status": "ok",
        "commit": os.environ.get("RENDER_GIT_COMMIT", "local"),
        "last_tick_at": last_tick.isoformat() if last_tick else None,
    }

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import services
from app.api import errors, households, internal, riders, supervisor, ussd, webhooks
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.check_deployable()
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactUssdToken) for f in access.filters):
        access.addFilter(_RedactUssdToken())
    services.ensure(settings)
    pool = create_pool(settings)
    # Fail the deploy if the database is unreachable, rather than serving /health.
    await pool.open(wait=True, timeout=15)
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="Engine", version="0.1.0", lifespan=lifespan)
errors.install(app)
for module in (households, riders, supervisor, webhooks, ussd, internal):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict[str, str]:
    # The deploy job polls this until `commit` matches the SHA it deployed.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT", "local")}

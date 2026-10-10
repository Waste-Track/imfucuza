import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import services
from app.api import errors, households, internal, riders, supervisor, webhooks
from app.config import get_settings
from app.db import create_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.check_deployable()
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
for module in (households, riders, supervisor, webhooks, internal):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict[str, str]:
    # The deploy job polls this until `commit` matches the SHA it deployed.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT", "local")}

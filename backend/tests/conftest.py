from collections.abc import AsyncIterator

import pytest

from app.db import Conn
from tests.helpers import connect


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def conn() -> AsyncIterator[Conn]:
    async with await connect() as c:
        yield c


@pytest.fixture
def world(monkeypatch):
    from fastapi.testclient import TestClient

    from app import services
    from app.adapters.fakes import FakePaymentProvider, FakeSmsGateway
    from app.auth import current_principal
    from app.config import get_settings
    from app.main import app
    from tests.flow import TICK_SECRET, USSD_TOKEN, Db, World, principal_from_headers

    monkeypatch.setenv("INTERNAL_SECRET", TICK_SECRET)
    monkeypatch.setenv("USSD_WEBHOOK_TOKEN", USSD_TOKEN)
    monkeypatch.setenv("USSD_PROVIDER", "africastalking")
    get_settings.cache_clear()
    fakes = services.Services(payments=FakePaymentProvider(), sms=FakeSmsGateway())
    services.install(fakes)
    app.dependency_overrides[current_principal] = principal_from_headers
    db = Db()
    # The database is shared between tests: start with an empty queue and
    # nobody on duty, so earlier tests' jobs and riders don't interfere.
    db.run("delete from engine.jobs")
    db.run("update engine.riders set on_duty = false")
    try:
        with TestClient(app) as client:
            yield World(client, fakes.payments, fakes.sms, db)
    finally:
        app.dependency_overrides.clear()
        services.uninstall()
        get_settings.cache_clear()
        db.conn.close()

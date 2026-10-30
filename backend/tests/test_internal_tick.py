import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import jobs
from app.config import Settings, get_settings
from app.main import _QuietTicks, app


@pytest.fixture
def client_with_secret():
    def use(secret: str) -> TestClient:
        app.dependency_overrides[get_settings] = lambda: Settings(internal_secret=SecretStr(secret))
        return TestClient(app)

    yield use
    app.dependency_overrides.clear()


def test_tick_is_hidden_until_a_secret_is_configured(client_with_secret):
    with client_with_secret("") as client:
        assert client.post("/internal/tick").status_code == 404


@pytest.mark.parametrize("header", [None, "wrong-secret", "sécret".encode()])
def test_tick_rejects_a_missing_or_wrong_secret(client_with_secret, header):
    headers = {"X-Internal-Secret": header} if header else {}

    with client_with_secret("cron-secret") as client:
        assert client.post("/internal/tick", headers=headers).status_code == 401


def test_tick_with_the_secret_drains_the_queue(client_with_secret):
    with client_with_secret("cron-secret") as client:
        response = client.post("/internal/tick", headers={"X-Internal-Secret": "cron-secret"})

    assert response.status_code == 200
    assert set(response.json()) == {"succeeded", "retried", "failed"}


def test_health_shows_when_the_queue_last_ran(client_with_secret):
    with client_with_secret("cron-secret") as client:
        before = client.get("/health").json()["last_tick_at"]
        client.post("/internal/tick", headers={"X-Internal-Secret": "cron-secret"})
        after = client.get("/health").json()["last_tick_at"]

    assert before is None
    assert after is not None


class _Held:
    def locked(self) -> bool:
        return True


def test_a_tick_is_skipped_while_another_is_running(client_with_secret, monkeypatch):
    ran = []

    async def run_due(*args, **kwargs):
        ran.append(True)

    monkeypatch.setattr(jobs, "run_due", run_due)
    with client_with_secret("cron-secret") as client:
        monkeypatch.setattr(client.app.state, "tick_lock", _Held())
        response = client.post("/internal/tick", headers={"X-Internal-Secret": "cron-secret"})

    assert response.json() == {"succeeded": 0, "retried": 0, "failed": 0}
    assert ran == []


@pytest.mark.parametrize(
    ("path", "status", "logged"),
    [("/internal/tick", 200, False), ("/internal/tick", 401, True), ("/health", 200, True)],
)
def test_only_ticks_that_fail_reach_the_access_log(path, status, logged):
    args = ("10.0.0.1:5000", "POST", path, "1.1", status)
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', args, None
    )

    assert _QuietTicks().filter(record) is logged


def test_ticks_keep_the_classifier_awake_without_calling_it_every_time(client_with_secret):
    from app import services

    with client_with_secret("cron-secret") as client:
        classifier = services.get().classifier
        before = classifier.warmed
        for _ in range(3):
            client.post("/internal/tick", headers={"X-Internal-Secret": "cron-secret"})

    assert classifier.warmed - before == 1

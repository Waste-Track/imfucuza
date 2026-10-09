import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import app


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

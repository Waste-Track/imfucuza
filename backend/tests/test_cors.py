import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app

WEB_APP = "https://imfucuza.vercel.app"


@pytest.fixture
def client(monkeypatch):
    # The middleware reads the list when the app is built, so patch the built copy.
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    monkeypatch.setitem(cors.kwargs, "allow_origins", [WEB_APP])
    app.middleware_stack = None
    yield TestClient(app)
    app.middleware_stack = None


def preflight(client, origin, headers="authorization,content-type,idempotency-key"):
    return client.options(
        "/v1/pickups",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": headers,
        },
    )


def test_listed_origin_may_call_the_api(client):
    response = preflight(client, WEB_APP)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == WEB_APP
    assert (
        client.get("/health", headers={"Origin": WEB_APP}).headers["access-control-allow-origin"]
        == WEB_APP
    )


def test_server_only_headers_are_refused(client):
    assert preflight(client, WEB_APP, headers="x-internal-secret").status_code == 400


def test_other_origins_get_no_cors_headers(client):
    response = preflight(client, "https://evil.example")

    assert response.status_code == 400
    assert (
        "access-control-allow-origin"
        not in client.get("/health", headers={"Origin": "https://evil.example"}).headers
    )


def test_origins_are_read_as_a_json_list(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f'["{WEB_APP}/", "http://localhost:3000"]')

    assert Settings(_env_file=None).cors_allowed_origins == [WEB_APP, "http://localhost:3000"]
    assert Settings(cors_allowed_origins=["HTTPS://Imfucuza.Vercel.App"]).cors_allowed_origins == [
        WEB_APP
    ]


@pytest.mark.parametrize(
    "bad", ["*", "imfucuza.vercel.app", f"{WEB_APP}/app", "https://*.vercel.app", f"{WEB_APP}?x"]
)
def test_anything_but_an_exact_origin_is_refused(bad):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_allowed_origins=[bad])

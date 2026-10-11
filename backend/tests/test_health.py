from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_ok_and_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["commit"] == "abc123"

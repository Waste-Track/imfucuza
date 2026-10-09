from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import ConnDep
from app.main import lifespan

app = FastAPI(lifespan=lifespan)


@app.post("/unbalanced")
async def unbalanced(conn: ConnDep) -> dict:
    # Passes every immediate check. The ledger rejects it only at commit.
    await conn.execute(
        """
        insert into engine.ledger_transactions (kind, unit, idempotency_key)
        values ('raw', 'GHS', gen_random_uuid()::text)
        """
    )
    return {"ok": True}


def test_a_failed_commit_is_an_error_response_not_a_success():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/unbalanced")

    assert response.status_code == 500

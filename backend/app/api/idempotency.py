"""Idempotency-Key handling: a retried request returns the first response
instead of doing the work twice."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from app.db import Conn


async def once(
    conn: Conn,
    *,
    user_id: UUID,
    key: str | None,
    request: dict[str, Any],
    work: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any]]:
    if not key or not 8 <= len(key) <= 200:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="send an Idempotency-Key header (8-200 characters)"
        )
    request_hash = hashlib.sha256(
        json.dumps(jsonable_encoder(request), sort_keys=True).encode()
    ).hexdigest()

    cur = await conn.execute(
        """
        select request_hash, response_status, response_body from engine.idempotency_keys
         where user_id = %s and key = %s
        """,
        (user_id, key),
    )
    seen = await cur.fetchone()
    if seen:
        if seen["request_hash"] != request_hash:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="this Idempotency-Key was used for a different request",
            )
        return seen["response_status"], seen["response_body"]

    code, body = await work()
    cur = await conn.execute(
        """
        insert into engine.idempotency_keys
            (user_id, key, request_hash, response_status, response_body)
        values (%s, %s, %s, %s, %s)
        on conflict do nothing
        returning key
        """,
        (user_id, key, request_hash, code, Jsonb(jsonable_encoder(body))),
    )
    if await cur.fetchone() is None:
        # A concurrent request with the same key finished first: undo ours.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="request already in progress")
    return code, body

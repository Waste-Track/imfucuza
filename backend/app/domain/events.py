from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db import Conn

ACTOR_TYPES = frozenset({"household", "rider", "supervisor", "admin", "system", "provider"})


async def record(
    conn: Conn,
    name: str,
    *,
    actor_type: str,
    actor_id: UUID | None = None,
    pickup_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append to the event log. Payloads carry IDs, never phone numbers or PINs."""
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unknown actor type {actor_type!r}")
    cur = await conn.execute(
        """
        insert into engine.events (name, actor_type, actor_id, pickup_id, payload)
        values (%s, %s, %s, %s, %s)
        returning id
        """,
        (name, actor_type, actor_id, pickup_id, Jsonb(payload or {})),
    )
    return (await cur.fetchone())["id"]

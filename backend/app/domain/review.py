"""The supervisor queue (engine-design.md section 6)."""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db import Conn
from app.domain import events


async def open_item(
    conn: Conn,
    type_: str,
    *,
    pickup_id: UUID | None = None,
    rider_id: UUID | None = None,
    household_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> UUID | None:
    """Open a review item. Returns None if the same problem (type and reason)
    is already open for the pickup."""
    cur = await conn.execute(
        """
        insert into engine.review_items (type, pickup_id, rider_id, household_id, payload)
        values (%s, %s, %s, %s, %s)
        on conflict (type, pickup_id, (coalesce(payload->>'reason', ''))) where status = 'open'
        do nothing
        returning id
        """,
        (type_, pickup_id, rider_id, household_id, Jsonb(payload or {})),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    await events.record(
        conn,
        "review.opened",
        actor_type="system",
        pickup_id=pickup_id,
        payload={"review_item_id": str(row["id"]), "type": type_},
    )
    return row["id"]

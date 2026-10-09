import secrets
from uuid import UUID

import anyio
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import Conn
from app.domain.pickups import Offering, initial_status

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


async def connect() -> Conn:
    """Autocommit, so each `conn.transaction()` in a test really commits: the
    ledger's balance checks only run at commit."""
    url = get_settings().database_url
    # Tests write permanent append-only rows and empty the job queue.
    if conninfo_to_dict(url).get("host") not in LOCAL_HOSTS:
        raise RuntimeError("tests only run against a local database")
    return await AsyncConnection.connect(
        url, row_factory=dict_row, prepare_threshold=None, autocommit=True
    )


async def wait_until_blocked(observer: Conn, blocked: Conn, timeout: float = 5.0) -> None:
    """Wait until `blocked` is waiting on a lock, so a concurrency test proves overlap."""
    pid = blocked.info.backend_pid
    with anyio.fail_after(timeout):
        while True:
            cur = await observer.execute(
                "select wait_event_type from pg_stat_activity where pid = %s", (pid,)
            )
            row = await cur.fetchone()
            if row and row["wait_event_type"] == "Lock":
                return
            await anyio.sleep(0.02)


async def make_household(conn: Conn) -> UUID:
    phone = "+2332" + "".join(secrets.choice("0123456789") for _ in range(8))
    async with conn.transaction():
        cur = await conn.execute(
            "insert into engine.users (role, phone_e164) values ('household', %s) returning id",
            (phone,),
        )
        user_id = (await cur.fetchone())["id"]
        cur = await conn.execute(
            "insert into engine.households (user_id) values (%s) returning id", (user_id,)
        )
        return (await cur.fetchone())["id"]


async def make_pickup(conn: Conn, offering: Offering) -> UUID:
    household_id = await make_household(conn)
    fee = 1000 if offering is Offering.REFUSE else None
    async with conn.transaction():
        cur = await conn.execute(
            """
            insert into engine.pickup_requests (household_id, offering, status, fee_pesewas)
            values (%s, %s, %s, %s) returning id
            """,
            (household_id, offering, initial_status(offering), fee),
        )
        return (await cur.fetchone())["id"]

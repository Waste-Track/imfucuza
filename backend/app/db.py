from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings

Conn = AsyncConnection[DictRow]


def create_pool(settings: Settings) -> AsyncConnectionPool[Conn]:
    return AsyncConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        open=False,
        # Server-side prepared statements break behind transaction-mode poolers.
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
    )


async def get_conn(request: Request) -> AsyncIterator[Conn]:
    """One connection and one database transaction per request."""
    pool: AsyncConnectionPool[Conn] = request.app.state.pool
    async with pool.connection() as conn, conn.transaction():
        yield conn


# scope="function" commits before the response is sent. The ledger checks
# balance at commit, so a failed commit must become an error response, not a
# 200 for work that was rolled back.
ConnDep = Annotated[Conn, Depends(get_conn, scope="function")]

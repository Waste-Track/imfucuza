from collections.abc import AsyncIterator

import pytest

from app.db import Conn
from tests.helpers import connect


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def conn() -> AsyncIterator[Conn]:
    async with await connect() as c:
        yield c

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest

from app import jobs
from app.config import get_settings
from app.db import create_pool
from app.domain import events

pytestmark = pytest.mark.anyio


@jobs.handler("test.record_event")
async def record_event(conn, payload):
    await events.record(conn, "test.job_ran", actor_type="system", payload=payload)


@jobs.handler("test.record_then_fail")
async def record_then_fail(conn, payload):
    await events.record(conn, "test.job_ran", actor_type="system", payload=payload)
    raise RuntimeError("provider timed out")


@jobs.handler("test.unbalanced_posting")
async def unbalanced_posting(conn, payload):
    cur = await conn.execute(
        """
        insert into engine.ledger_transactions (kind, unit, idempotency_key)
        values ('raw', 'GHS', %s) returning id
        """,
        (payload["key"],),
    )
    assert await cur.fetchone()


@jobs.handler("test.slow")
async def slow(conn, payload):
    await anyio.sleep(payload.get("seconds", 1))
    await events.record(conn, "test.job_ran", actor_type="system", payload=payload)


@jobs.handler("test.slow_query")
async def slow_query(conn, payload):
    await conn.execute("select pg_sleep(5)")


@jobs.handler("test.duplicate_phone")
async def duplicate_phone(conn, payload):
    for _ in range(2):
        await conn.execute(
            "insert into engine.users (role, phone_e164) values ('household', %s)",
            (payload["phone"],),
        )


@pytest.fixture
async def pool() -> AsyncIterator:
    p = create_pool(get_settings())
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
async def empty_queue(conn):
    """The queue is shared, so each test starts with it empty."""
    await conn.execute("delete from engine.jobs")


async def job_row(conn, job_id):
    cur = await conn.execute("select * from engine.jobs where id = %s", (job_id,))
    return await cur.fetchone()


async def ran_count(conn, tag):
    cur = await conn.execute(
        """
        select count(*) as n from engine.events
         where name = 'test.job_ran' and payload->>'tag' = %s
        """,
        (tag,),
    )
    return (await cur.fetchone())["n"]


async def test_due_job_runs_once_and_is_marked_done(conn, pool):
    tag = str(uuid4())
    job_id = await jobs.enqueue(conn, "test.record_event", {"tag": tag})

    first = await jobs.run_due(pool, limit=10)
    second = await jobs.run_due(pool, limit=10)

    assert (first.succeeded, second.ran) == (1, 0)
    assert await ran_count(conn, tag) == 1
    row = await job_row(conn, job_id)
    assert row["done_at"] is not None and row["attempts"] == 1


async def test_future_job_waits(conn, pool):
    await jobs.enqueue(conn, "test.record_event", {}, run_at=datetime.now(UTC) + timedelta(hours=1))

    assert (await jobs.run_due(pool, limit=10)).ran == 0


async def test_dedupe_key_queues_a_job_only_once(conn):
    key = f"offer-expiry:{uuid4()}"

    assert await jobs.enqueue(conn, "test.record_event", dedupe_key=key) is not None
    assert await jobs.enqueue(conn, "test.record_event", dedupe_key=key) is None


async def test_failed_attempt_rolls_back_its_work_and_retries_later(conn, pool):
    tag = str(uuid4())
    job_id = await jobs.enqueue(conn, "test.record_then_fail", {"tag": tag})

    report = await jobs.run_due(pool, limit=10)

    assert report.retried == 1
    assert await ran_count(conn, tag) == 0
    row = await job_row(conn, job_id)
    assert row["attempts"] == 1
    assert "provider timed out" in row["last_error"]
    assert row["run_at"] > datetime.now(UTC)


async def test_job_fails_permanently_after_its_last_attempt(conn, pool):
    job_id = await jobs.enqueue(conn, "test.record_then_fail", {"tag": "x"}, max_attempts=1)

    report = await jobs.run_due(pool, limit=10)

    assert report.failed == 1
    assert (await job_row(conn, job_id))["failed_at"] is not None


async def test_job_without_a_handler_fails_instead_of_blocking_the_queue(conn, pool):
    job_id = await jobs.enqueue(conn, "test.no_such_handler", max_attempts=1)

    assert (await jobs.run_due(pool, limit=10)).failed == 1
    assert "no handler" in (await job_row(conn, job_id))["last_error"]


async def test_ledger_violation_found_at_commit_counts_as_a_failed_attempt(conn, pool):
    key = f"raw-job:{uuid4()}"
    job_id = await jobs.enqueue(conn, "test.unbalanced_posting", {"key": key})

    report = await jobs.run_due(pool, limit=10)

    assert report.retried == 1
    assert "unbalanced" in (await job_row(conn, job_id))["last_error"]
    cur = await conn.execute(
        "select count(*) as n from engine.ledger_transactions where idempotency_key = %s", (key,)
    )
    assert (await cur.fetchone())["n"] == 0


async def test_concurrent_ticks_never_run_a_job_twice(conn, pool):
    tag = str(uuid4())
    for i in range(10):
        await jobs.enqueue(conn, "test.slow", {"tag": tag, "n": i, "seconds": 0.05})
    reports = []

    async def tick():
        reports.append(await jobs.run_due(pool, limit=10))

    async with anyio.create_task_group() as tg:
        tg.start_soon(tick)
        tg.start_soon(tick)

    assert sum(r.succeeded for r in reports) == 10
    assert all(r.succeeded >= 1 for r in reports), "both ticks should have done work"
    assert await ran_count(conn, tag) == 10


async def test_backoff_doubles_and_is_capped():
    assert [jobs.backoff(n).total_seconds() for n in (1, 2, 3, 10)] == [30, 60, 120, 1800]


async def test_crashed_attempt_still_counts_and_the_job_runs_after_its_lease(conn, pool):
    tag = str(uuid4())
    job_id = await jobs.enqueue(conn, "test.record_event", {"tag": tag})
    await jobs._claim(pool)  # a tick claims the job, then dies

    assert (await jobs.run_due(pool, limit=10)).ran == 0, "lease still held"

    await conn.execute(
        "update engine.jobs set locked_until = now() - interval '1 second' where id = %s",
        (job_id,),
    )
    assert (await jobs.run_due(pool, limit=10)).succeeded == 1
    assert (await job_row(conn, job_id))["attempts"] == 2
    assert await ran_count(conn, tag) == 1


async def test_job_that_keeps_crashing_fails_and_is_reported(conn, pool):
    job_id = await jobs.enqueue(conn, "test.record_event", {}, max_attempts=1)
    await jobs._claim(pool)
    await conn.execute(
        "update engine.jobs set locked_until = now() - interval '1 second' where id = %s",
        (job_id,),
    )

    assert (await jobs.run_due(pool, limit=10)).failed == 1
    cur = await conn.execute(
        """
        select count(*) as n from engine.events
         where name = 'job.failed' and payload->>'job_id' = %s
        """,
        (str(job_id),),
    )
    assert (await cur.fetchone())["n"] == 1


async def test_slow_handler_times_out(conn, pool):
    tag = str(uuid4())
    job_id = await jobs.enqueue(conn, "test.slow", {"tag": tag, "seconds": 2})

    report = await jobs.run_due(pool, limit=10, job_timeout_s=0.1)

    assert report.retried == 1
    assert "TimeoutError" in (await job_row(conn, job_id))["last_error"]
    assert await ran_count(conn, tag) == 0


async def test_timed_out_query_leaves_the_pool_usable(conn, pool):
    job_id = await jobs.enqueue(conn, "test.slow_query")
    assert (await jobs.run_due(pool, limit=1, job_timeout_s=0.2)).retried == 1
    assert "statement timeout" in (await job_row(conn, job_id))["last_error"]

    tag = str(uuid4())
    await jobs.enqueue(conn, "test.record_event", {"tag": tag})
    assert (await jobs.run_due(pool, limit=10)).succeeded == 1
    assert await ran_count(conn, tag) == 1


async def test_errors_never_record_row_values_such_as_phone_numbers(conn, pool):
    phone = "+2335" + str(uuid4().int)[:8]
    job_id = await jobs.enqueue(conn, "test.duplicate_phone", {"phone": phone}, max_attempts=1)

    await jobs.run_due(pool, limit=10)

    error = (await job_row(conn, job_id))["last_error"]
    assert "users_phone_e164_key" in error
    assert phone not in error

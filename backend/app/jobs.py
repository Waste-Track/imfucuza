"""Background jobs stored in Postgres and drained by POST /internal/tick.

A job is first claimed in a short transaction that counts the attempt and
takes a lease. It then runs in a second transaction together with its
completion record. Its database effects therefore happen exactly once, and a
crash, hang or lost connection still uses up an attempt. External calls made
by a handler (SMS, payments) can repeat, so handlers must be idempotent
towards providers.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import anyio
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.adapters.base import ProviderError
from app.db import Conn
from app.domain import events

log = logging.getLogger(__name__)

Handler = Callable[[Conn, dict[str, Any]], Awaitable[None]]
# Called in the same transaction when a job fails for good, with its payload and error.
GiveUp = Callable[[Conn, dict[str, Any], str], Awaitable[None]]

JOB_TIMEOUT_S = 30.0
TICK_BUDGET_S = 10.0
# Longer than a job can run, so a lease only expires once its tick is gone.
LEASE = timedelta(minutes=2)

_handlers: dict[str, Handler] = {}
_give_up_hooks: dict[str, GiveUp] = {}


def handler(kind: str, *, on_give_up: GiveUp | None = None) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if kind in _handlers:
            raise RuntimeError(f"job kind {kind!r} already has a handler")
        _handlers[kind] = fn
        if on_give_up:
            _give_up_hooks[kind] = on_give_up
        return fn

    return register


def backoff(attempts: int) -> timedelta:
    """30 s, 1 min, 2 min, ... capped at 30 min."""
    return timedelta(seconds=min(30 * 2 ** (attempts - 1), 1800))


async def enqueue(
    conn: Conn,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> int | None:
    """Queue a job. Returns None if a job with the same dedupe key exists."""
    cur = await conn.execute(
        """
        insert into engine.jobs (kind, payload, run_at, dedupe_key, max_attempts)
        values (%s, %s, coalesce(%s, now()), %s, %s)
        on conflict (dedupe_key) do nothing
        returning id
        """,
        (kind, Jsonb(payload or {}), run_at, dedupe_key, max_attempts),
    )
    row = await cur.fetchone()
    return row["id"] if row else None


@dataclass
class TickReport:
    succeeded: int = 0
    retried: int = 0
    failed: int = 0

    @property
    def ran(self) -> int:
        return self.succeeded + self.retried + self.failed

    def add(self, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)


async def run_due(
    pool: AsyncConnectionPool[Conn],
    limit: int,
    *,
    budget_s: float = TICK_BUDGET_S,
    job_timeout_s: float = JOB_TIMEOUT_S,
) -> TickReport:
    report = TickReport()
    deadline = time.monotonic() + budget_s
    while report.ran < limit and time.monotonic() < deadline:
        job = await _claim(pool)
        if job is None:
            break
        report.add(await _work(pool, job, job_timeout_s))
    return report


async def _claim(pool: AsyncConnectionPool[Conn]) -> dict[str, Any] | None:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            update engine.jobs
               set attempts = attempts + 1, locked_until = now() + %s
             where id = (
                   select id from engine.jobs
                    where done_at is null and failed_at is null and run_at <= now()
                      and (locked_until is null or locked_until < now())
                    order by run_at
                    limit 1
                      for update skip locked)
            returning id, kind, payload, attempts, max_attempts
            """,
            (LEASE,),
        )
        return await cur.fetchone()


async def _work(pool: AsyncConnectionPool[Conn], job: dict[str, Any], timeout_s: float) -> str:
    try:
        async with pool.connection() as conn, conn.transaction():
            # Postgres cancels a slow query itself, just before the job's time
            # limit, so the connection stays usable and the error is recorded.
            await conn.execute(
                "select set_config('statement_timeout', %s, true)", (f"{int(timeout_s * 800)}ms",)
            )
            if job["attempts"] > job["max_attempts"]:
                await _give_up(conn, job, "previous attempt crashed or timed out")
                return "failed"

            fn = _handlers.get(job["kind"])
            try:
                if fn is None:
                    raise LookupError(f"no handler for job kind {job['kind']!r}")
                async with conn.transaction():
                    # Only the handler is inside the time limit: anyio cancels
                    # every await in an expired scope, including the rollback.
                    with anyio.fail_after(timeout_s):
                        await fn(conn, job["payload"])
                    # Run deferred ledger checks now, so a failure counts as
                    # this attempt instead of failing the final commit.
                    await conn.execute("set constraints all immediate")
            except Exception as exc:
                error = describe(exc)
                # A provider that refused for good would refuse every retry too.
                permanent = isinstance(exc, ProviderError) and not exc.retryable
                if permanent or job["attempts"] >= job["max_attempts"]:
                    await _give_up(conn, job, error)
                    return "failed"
                log.warning(
                    "job %s (%s) attempt %s: %s", job["id"], job["kind"], job["attempts"], error
                )
                await conn.execute(
                    """
                    update engine.jobs
                       set last_error = %s, locked_until = null, run_at = now() + %s
                     where id = %s
                    """,
                    (error, backoff(job["attempts"]), job["id"]),
                )
                return "retried"

            await conn.execute(
                "update engine.jobs set done_at = now(), locked_until = null where id = %s",
                (job["id"],),
            )
            return "succeeded"
    except Exception as exc:
        # The work transaction itself broke (lost connection, failed commit).
        # The attempt was already counted when the job was claimed.
        error = describe(exc)
        log.error("job %s (%s) transaction failed: %s", job["id"], job["kind"], error)
        await _record_broken_attempt(pool, job, error)
        return "retried"


async def _record_broken_attempt(
    pool: AsyncConnectionPool[Conn], job: dict[str, Any], error: str
) -> None:
    """Best effort, on a fresh connection. If this fails too, the lease expires
    and the job runs again anyway."""
    try:
        async with pool.connection() as conn, conn.transaction():
            await conn.execute(
                """
                update engine.jobs
                   set last_error = %s, locked_until = null, run_at = now() + %s
                 where id = %s and done_at is null
                """,
                (error, backoff(job["attempts"]), job["id"]),
            )
    except Exception as exc:
        log.error("job %s: could not record the failed attempt: %s", job["id"], describe(exc))


async def _give_up(conn: Conn, job: dict[str, Any], error: str) -> None:
    log.error("job %s (%s) failed permanently: %s", job["id"], job["kind"], error)
    await conn.execute(
        """
        update engine.jobs
           set last_error = %s, failed_at = now(), locked_until = null
         where id = %s
        """,
        (error, job["id"]),
    )
    await events.record(
        conn,
        "job.failed",
        actor_type="system",
        payload={"job_id": job["id"], "kind": job["kind"], "attempts": job["attempts"]},
    )
    if hook := _give_up_hooks.get(job["kind"]):
        await hook(conn, job["payload"], error)


def describe(exc: BaseException) -> str:
    """Error text for logs and jobs.last_error. Database errors keep only the
    primary message: their detail lines can quote row values such as phone
    numbers."""
    if isinstance(exc, psycopg.Error):
        diag = exc.diag
        parts = [type(exc).__name__, f"sqlstate={exc.sqlstate}"]
        if diag.constraint_name:
            parts.append(f"constraint={diag.constraint_name}")
        parts.append(diag.message_primary or "")
        return " ".join(parts)[:1000]
    if isinstance(exc, TimeoutError):
        return "TimeoutError: job exceeded its time limit"
    return f"{type(exc).__name__}: {exc}"[:1000]

"""Supabase Cron calling POST /internal/tick, checked inside the database.
Every test rolls back, so no secret is left behind and no request is sent."""

import pytest

from tests.helpers import connect

pytestmark = pytest.mark.anyio

URL, SECRET = "engine_tick_url", "engine_internal_secret"


async def schedule(name: str) -> dict:
    conn = await connect()
    try:
        cur = await conn.execute(
            "select schedule, command, active, username from cron.job where jobname = %s",
            (name,),
        )
        return await cur.fetchone()
    finally:
        await conn.close()


async def test_the_queue_is_ticked_every_10_seconds():
    job = await schedule("engine-tick")

    assert job == {
        "schedule": "10 seconds",
        "command": "select engine.tick()",
        "active": True,
        "username": "postgres",
    }


async def test_cron_run_history_older_than_a_week_is_deleted():
    job = await schedule("engine-cron-history")
    conn = await connect()
    try:
        async with conn.transaction(force_rollback=True):
            # Negative run ids: pg_cron's sequence never issues them.
            old, recent = -8, -6
            await conn.execute(
                """
                insert into cron.job_run_details (runid, username, status, end_time)
                values (%s, current_user, 'succeeded', now() - interval '8 days'),
                       (%s, current_user, 'succeeded', now() - interval '6 days')
                """,
                (old, recent),
            )
            await conn.execute(job["command"])
            cur = await conn.execute(
                "select runid from cron.job_run_details where runid in (%s, %s)", (old, recent)
            )
            kept = [row["runid"] for row in await cur.fetchall()]
    finally:
        await conn.close()

    assert job["active"]
    assert kept == [recent]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
async def test_client_roles_cannot_trigger_a_tick(role):
    conn = await connect()
    try:
        cur = await conn.execute(
            "select has_function_privilege(%s, 'engine.tick()', 'execute') as allowed", (role,)
        )
        allowed = (await cur.fetchone())["allowed"]
    finally:
        await conn.close()

    assert allowed is False


async def tick_with_secrets(**secrets: str) -> dict | None:
    conn = await connect()
    try:
        async with conn.transaction(force_rollback=True):
            await conn.execute("delete from vault.secrets where name in (%s, %s)", (URL, SECRET))
            for name, value in secrets.items():
                await conn.execute("select vault.create_secret(%s, %s)", (value, name))
            cur = await conn.execute("select engine.tick() as id")
            request_id = (await cur.fetchone())["id"]
            if request_id is None:
                return None
            cur = await conn.execute(
                "select method, url, headers, timeout_milliseconds"
                "  from net.http_request_queue where id = %s",
                (request_id,),
            )
            return await cur.fetchone()
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "secrets", [{}, {URL: "https://engine.test/internal/tick"}, {SECRET: "s3cret-for-this-test"}]
)
async def test_nothing_is_sent_until_the_url_and_secret_are_both_set(secrets):
    assert await tick_with_secrets(**secrets) is None


async def test_tick_posts_to_the_engine_with_the_internal_secret():
    request = await tick_with_secrets(
        **{URL: "https://engine.test/internal/tick", SECRET: "s3cret-for-this-test"}
    )

    assert request["method"] == "POST"
    assert request["url"] == "https://engine.test/internal/tick"
    assert request["headers"]["x-internal-secret"] == "s3cret-for-this-test"
    assert request["timeout_milliseconds"] == 60000

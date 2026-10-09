from uuid import uuid4

import anyio
import pytest
from psycopg import errors

from app.domain import pickups
from app.domain.pickups import Event, InvalidTransition, Offering, StaleVersion, Status
from tests.helpers import connect, make_pickup, wait_until_blocked

pytestmark = pytest.mark.anyio


async def status_and_version(conn, pickup_id):
    cur = await conn.execute(
        "select status, version, completed_at from engine.pickup_requests where id = %s",
        (pickup_id,),
    )
    return await cur.fetchone()


async def test_transition_updates_status_version_and_event_log(conn):
    pickup_id = await make_pickup(conn, Offering.REFUSE)

    async with conn.transaction():
        result = await pickups.apply(
            conn, pickup_id, Event.PAYMENT_SUCCEEDED, expected_version=0, actor_type="provider"
        )

    assert (result.previous, result.status, result.version) == (
        Status.AWAITING_PAYMENT,
        Status.PENDING_DISPATCH,
        1,
    )
    row = await status_and_version(conn, pickup_id)
    assert (row["status"], row["version"]) == ("pending_dispatch", 1)
    cur = await conn.execute(
        "select name, actor_type, payload from engine.events where pickup_id = %s", (pickup_id,)
    )
    event = await cur.fetchone()
    assert event["name"] == "pickup.payment_succeeded"
    assert event["payload"] == {"from": "awaiting_payment", "to": "pending_dispatch"}


async def test_stale_version_is_rejected_and_changes_nothing(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)

    with pytest.raises(StaleVersion):
        async with conn.transaction():
            await pickups.apply(
                conn, pickup_id, Event.OFFER_SENT, expected_version=3, actor_type="system"
            )

    row = await status_and_version(conn, pickup_id)
    assert (row["status"], row["version"]) == ("pending_dispatch", 0)


async def test_invalid_transition_is_rejected_and_changes_nothing(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)

    with pytest.raises(InvalidTransition):
        async with conn.transaction():
            await pickups.apply(
                conn, pickup_id, Event.PIN_CONFIRMED, expected_version=0, actor_type="household"
            )

    row = await status_and_version(conn, pickup_id)
    assert (row["status"], row["version"]) == ("pending_dispatch", 0)


async def test_transition_can_only_set_allow_listed_columns(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)

    with pytest.raises(ValueError, match="cannot set"):
        async with conn.transaction():
            await pickups.apply(
                conn,
                pickup_id,
                Event.OFFER_SENT,
                expected_version=0,
                actor_type="system",
                changes={"fee_pesewas": 0},
            )


async def test_completing_a_pickup_records_completion_time(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)
    path = [
        Event.OFFER_SENT,
        Event.OFFER_ACCEPTED,
        Event.RIDER_ARRIVED,
        Event.PHOTO_SUBMITTED,
        Event.VERIFICATION_ACCEPTED,
        Event.PIN_CONFIRMED,
    ]

    for version, event in enumerate(path):
        async with conn.transaction():
            await pickups.apply(
                conn, pickup_id, event, expected_version=version, actor_type="system"
            )

    row = await status_and_version(conn, pickup_id)
    assert row["status"] == "completed"
    assert row["completed_at"] is not None


async def test_unknown_pickup_is_reported(conn):
    with pytest.raises(LookupError):
        async with conn.transaction():
            await pickups.apply(
                conn, uuid4(), Event.OFFER_SENT, expected_version=0, actor_type="system"
            )


async def test_event_log_is_append_only(conn):
    pickup_id = await make_pickup(conn, Offering.REFUSE)
    async with conn.transaction():
        await pickups.apply(
            conn, pickup_id, Event.PAYMENT_FAILED, expected_version=0, actor_type="provider"
        )

    with pytest.raises(errors.InsufficientPrivilege, match="append-only"):
        async with conn.transaction():
            await conn.execute("delete from engine.events where pickup_id = %s", (pickup_id,))


async def test_transition_sets_allow_listed_columns(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)
    for version, event in enumerate([Event.OFFER_SENT, Event.OFFER_ACCEPTED, Event.RIDER_ARRIVED]):
        await pickups.apply(conn, pickup_id, event, expected_version=version, actor_type="system")

    await pickups.apply(
        conn,
        pickup_id,
        Event.PHOTO_SUBMITTED,
        expected_version=3,
        actor_type="rider",
        changes={"weight_g": 4200, "verification_outcome": "pending"},
    )

    cur = await conn.execute(
        "select weight_g, verification_outcome from engine.pickup_requests where id = %s",
        (pickup_id,),
    )
    assert await cur.fetchone() == {"weight_g": 4200, "verification_outcome": "pending"}


async def test_concurrent_transitions_from_one_version_let_only_one_win(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)
    outcome = {}

    async def second(other):
        try:
            await pickups.apply(
                other, pickup_id, Event.OFFER_SENT, expected_version=0, actor_type="system"
            )
            outcome["second"] = "applied"
        except StaleVersion:
            outcome["second"] = "stale"

    async with await connect() as first, await connect() as other:
        async with anyio.create_task_group() as tg:
            async with first.transaction():
                await pickups.apply(
                    first, pickup_id, Event.OFFER_SENT, expected_version=0, actor_type="system"
                )
                tg.start_soon(second, other)
                await wait_until_blocked(conn, other)

    assert outcome["second"] == "stale"
    assert (await status_and_version(conn, pickup_id))["version"] == 1


async def test_caller_payload_cannot_rewrite_the_audit_trail(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)

    await pickups.apply(
        conn,
        pickup_id,
        Event.OFFER_SENT,
        expected_version=0,
        actor_type="system",
        payload={"from": "completed", "to": "completed", "offer_id": "o-1"},
    )

    cur = await conn.execute("select payload from engine.events where pickup_id = %s", (pickup_id,))
    assert (await cur.fetchone())["payload"] == {
        "offer_id": "o-1",
        "from": "pending_dispatch",
        "to": "offered",
    }


async def test_unknown_actor_type_is_rejected_before_anything_changes(conn):
    pickup_id = await make_pickup(conn, Offering.PLASTIC)

    with pytest.raises(ValueError, match="actor type"):
        await pickups.apply(conn, pickup_id, Event.OFFER_SENT, expected_version=0, actor_type="bot")

    assert (await status_and_version(conn, pickup_id))["version"] == 0


async def test_supervisor_can_cancel_after_arrival(conn):
    pickup_id = await make_pickup(conn, Offering.REFUSE)
    path = [Event.PAYMENT_SUCCEEDED, Event.OFFER_SENT, Event.OFFER_ACCEPTED, Event.RIDER_ARRIVED]
    for version, event in enumerate(path):
        await pickups.apply(conn, pickup_id, event, expected_version=version, actor_type="system")

    result = await pickups.apply(
        conn,
        pickup_id,
        Event.CANCELLED_BY_SUPERVISOR,
        expected_version=4,
        actor_type="supervisor",
    )

    assert result.status is Status.CANCELLED

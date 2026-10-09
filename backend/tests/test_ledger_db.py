import random
from uuid import UUID, uuid4

import anyio
import pytest
from psycopg import errors

from app.domain import ledger
from app.domain.ledger import (
    ESCROW,
    PLATFORM_REVENUE,
    PROVIDER_CLEARING,
    REFUND_PAYABLE,
    RIDER_PAYABLE,
    Account,
    Posting,
    credit,
    debit,
)
from app.domain.pickups import Offering
from tests.helpers import connect, make_pickup, wait_until_blocked

pytestmark = pytest.mark.anyio

CLEARING = Account(PROVIDER_CLEARING)
REVENUE = Account(PLATFORM_REVENUE)


def escrow(owner: UUID) -> Account:
    return Account(ESCROW, owner)


def hold(owner: UUID, amount: int, key: str | None = None) -> Posting:
    return Posting(
        "fund_hold",
        key or f"hold:{owner}",
        (debit(CLEARING, amount), credit(escrow(owner), amount)),
    )


def release(owner: UUID, rider: UUID, amount: int, key: str) -> Posting:
    rider_share = amount * 7 // 10
    return Posting(
        "release",
        key,
        (
            debit(escrow(owner), amount),
            credit(Account(RIDER_PAYABLE, rider), rider_share),
            credit(REVENUE, amount - rider_share),
        ),
    )


async def test_posting_moves_balances_on_each_accounts_normal_side(conn):
    owner, rider = uuid4(), uuid4()

    await ledger.post(conn, hold(owner, 1000))
    assert await ledger.balance(conn, escrow(owner)) == 1000

    await ledger.post(conn, release(owner, rider, 1000, f"release:{owner}"))
    assert await ledger.balance(conn, escrow(owner)) == 0
    assert await ledger.balance(conn, Account(RIDER_PAYABLE, rider)) == 700


async def test_replaying_a_key_returns_the_original_transaction(conn):
    owner = uuid4()

    first = await ledger.post(conn, hold(owner, 1000))
    again = await ledger.post(conn, hold(owner, 1000))

    assert (again.txn_id, again.replayed) == (first.txn_id, True)
    assert await ledger.balance(conn, escrow(owner)) == 1000


async def test_reusing_a_key_for_different_legs_is_rejected(conn):
    owner = uuid4()
    await ledger.post(conn, hold(owner, 1000))

    with pytest.raises(ledger.IdempotencyConflict):
        await ledger.post(conn, hold(owner, 999))


async def test_overdraw_is_rejected_and_records_nothing(conn):
    owner, rider = uuid4(), uuid4()
    await ledger.post(conn, hold(owner, 500))
    key = f"release:{owner}"

    with pytest.raises(ledger.InsufficientBalance):
        await ledger.post(conn, release(owner, rider, 600, key))

    assert await ledger.balance(conn, escrow(owner)) == 500
    cur = await conn.execute(
        "select count(*) as n from engine.ledger_transactions where idempotency_key = %s", (key,)
    )
    assert (await cur.fetchone())["n"] == 0


async def test_two_concurrent_releases_cannot_both_drain_one_escrow(conn):
    owner = uuid4()
    await ledger.post(conn, hold(owner, 1000))
    outcome = {}

    async def second_release(other):
        try:
            await ledger.post(other, release(owner, uuid4(), 1000, f"release-b:{owner}"))
            outcome["second"] = "posted"
        except ledger.InsufficientBalance:
            outcome["second"] = "insufficient"

    async with await connect() as first, await connect() as other:
        async with anyio.create_task_group() as tg:
            async with first.transaction():
                await ledger.post(first, release(owner, uuid4(), 1000, f"release-a:{owner}"))
                tg.start_soon(second_release, other)
                # Prove the two really overlap: the second waits on our lock.
                await wait_until_blocked(conn, other)

    assert outcome["second"] == "insufficient"
    assert await ledger.balance(conn, escrow(owner)) == 0


async def test_database_rejects_an_unbalanced_transaction_at_commit(conn):
    owner = uuid4()
    await ledger.post(conn, hold(owner, 10))
    escrow_id = await _account_id(conn, escrow(owner))

    with pytest.raises(errors.CheckViolation, match="unbalanced"):
        async with conn.transaction():
            cur = await conn.execute(
                """
                insert into engine.ledger_transactions (kind, unit, idempotency_key)
                values ('raw', 'GHS', %s) returning id
                """,
                (f"raw:{owner}",),
            )
            txn_id = (await cur.fetchone())["id"]
            await conn.execute(
                """
                insert into engine.ledger_entries (txn_id, account_id, side, amount)
                values (%s, %s, 'debit', 5)
                """,
                (txn_id, escrow_id),
            )


async def test_database_rejects_an_entry_in_the_wrong_unit(conn):
    owner = uuid4()
    await ledger.post(conn, hold(owner, 10))
    escrow_id = await _account_id(conn, escrow(owner))

    with pytest.raises(errors.CheckViolation, match="does not match"):
        async with conn.transaction():
            cur = await conn.execute(
                """
                insert into engine.ledger_transactions (kind, unit, idempotency_key)
                values ('raw', 'PTS', %s) returning id
                """,
                (f"raw-unit:{owner}",),
            )
            txn_id = (await cur.fetchone())["id"]
            await conn.execute(
                """
                insert into engine.ledger_entries (txn_id, account_id, side, amount)
                values (%s, %s, 'credit', 5)
                """,
                (txn_id, escrow_id),
            )


@pytest.mark.parametrize(
    "statement",
    [
        "update engine.ledger_entries set amount = amount + 1 where txn_id = %(txn)s",
        "delete from engine.ledger_entries where txn_id = %(txn)s",
        "update engine.ledger_transactions set memo = 'edited' where id = %(txn)s",
        "delete from engine.ledger_transactions where id = %(txn)s",
    ],
)
async def test_ledger_history_is_append_only(conn, statement):
    result = await ledger.post(conn, hold(uuid4(), 10))

    with pytest.raises(errors.InsufficientPrivilege, match="append-only"):
        async with conn.transaction():
            await conn.execute(statement, {"txn": result.txn_id})


async def test_balances_change_only_through_entries(conn):
    owner = uuid4()
    await ledger.post(conn, hold(owner, 10))

    with pytest.raises(errors.InsufficientPrivilege, match="only through ledger entries"):
        async with conn.transaction():
            await conn.execute(
                "update engine.ledger_accounts set balance = 1000000 where code = %s",
                (escrow(owner).code,),
            )


async def test_random_postings_keep_every_balance_equal_to_its_entries(conn):
    rng = random.Random(591)
    owners = [uuid4() for _ in range(4)]
    accounts = [escrow(o) for o in owners] + [Account(RIDER_PAYABLE, o) for o in owners]

    for i in range(60):
        source, target = rng.sample(accounts, 2)
        amount = rng.randint(1, 500)
        if rng.random() < 0.4:
            posting = Posting(
                "fund_hold",
                f"rnd:{owners[0]}:{i}",
                (debit(CLEARING, amount), credit(source, amount)),
            )
        else:
            posting = Posting(
                "move", f"rnd:{owners[0]}:{i}", (debit(source, amount), credit(target, amount))
            )
        try:
            await ledger.post(conn, posting)
        except ledger.InsufficientBalance:
            pass

    cur = await conn.execute(
        """
        select a.code, a.balance,
               coalesce(sum(case when e.side = a.normal_side then e.amount else -e.amount end), 0)
                 as from_entries
          from engine.ledger_accounts a
          left join engine.ledger_entries e on e.account_id = a.id
         where a.code = any(%s)
         group by a.code, a.balance
        """,
        ([a.code for a in accounts],),
    )
    rows = await cur.fetchall()
    assert rows
    for row in rows:
        assert row["balance"] == row["from_entries"] >= 0, row["code"]


async def _account_id(conn, account: Account) -> int:
    cur = await conn.execute(
        "select id from engine.ledger_accounts where code = %s", (account.code,)
    )
    return (await cur.fetchone())["id"]


async def test_account_must_start_at_zero(conn):
    with pytest.raises(errors.InsufficientPrivilege, match="must start at zero"):
        await conn.execute(
            """
            insert into engine.ledger_accounts (code, unit, normal_side, balance)
            values (%s, 'GHS', 'credit', 50000)
            """,
            (escrow(uuid4()).code,),
        )


async def test_committed_transaction_cannot_take_more_entries(conn):
    owner = uuid4()
    result = await ledger.post(conn, hold(owner, 100))
    clearing_id = await _account_id(conn, CLEARING)
    escrow_id = await _account_id(conn, escrow(owner))

    with pytest.raises(errors.InsufficientPrivilege, match="already committed"):
        async with conn.transaction():
            for account_id, side in ((clearing_id, "debit"), (escrow_id, "credit")):
                await conn.execute(
                    """
                    insert into engine.ledger_entries (txn_id, account_id, side, amount)
                    values (%s, %s, %s, 5)
                    """,
                    (result.txn_id, account_id, side),
                )


@pytest.mark.parametrize(
    "statement",
    [
        "update engine.ledger_accounts set allow_negative = true where code = %(code)s",
        "update engine.ledger_accounts set normal_side = 'debit' where code = %(code)s",
        "delete from engine.ledger_accounts where code = %(code)s",
    ],
)
async def test_account_settings_are_fixed_once_created(conn, statement):
    owner = uuid4()
    await ledger.post(conn, hold(owner, 10))

    with pytest.raises(errors.InsufficientPrivilege):
        async with conn.transaction():
            await conn.execute(statement, {"code": escrow(owner).code})


@pytest.mark.parametrize(
    "table", ["ledger_entries", "ledger_transactions", "ledger_accounts", "events"]
)
async def test_ledger_and_event_tables_cannot_be_truncated(conn, table):
    with pytest.raises(errors.InsufficientPrivilege):
        async with conn.transaction():
            await conn.execute(f"truncate engine.{table} cascade")  # noqa: S608


async def test_a_pickup_is_settled_at_most_once(conn):
    pickup_id = await make_pickup(conn, Offering.REFUSE)
    household = uuid4()
    await ledger.post(conn, hold(pickup_id, 2000))
    await ledger.post(
        conn,
        Posting(
            "release",
            f"release:{pickup_id}",
            (debit(escrow(pickup_id), 1000), credit(REVENUE, 1000)),
            pickup_id=pickup_id,
        ),
    )

    with pytest.raises(errors.UniqueViolation):
        await ledger.post(
            conn,
            Posting(
                "refund_due",
                f"refund:{pickup_id}",
                (debit(escrow(pickup_id), 1000), credit(Account(REFUND_PAYABLE, household), 1000)),
                pickup_id=pickup_id,
            ),
        )


async def test_replay_for_a_different_pickup_is_rejected(conn):
    first, second = (
        await make_pickup(conn, Offering.REFUSE),
        await make_pickup(conn, Offering.REFUSE),
    )
    owner = uuid4()
    legs = (debit(CLEARING, 10), credit(escrow(owner), 10))
    await ledger.post(conn, Posting("fund_hold", f"pay:{owner}", legs, pickup_id=first))

    with pytest.raises(ledger.IdempotencyConflict):
        await ledger.post(conn, Posting("fund_hold", f"pay:{owner}", legs, pickup_id=second))


async def test_whole_ledger_reconciles(conn):
    """Runs after the tests above in this module: every balance in the
    database still equals its entries, and debits equal credits per unit."""
    cur = await conn.execute("select * from engine.ledger_reconcile()")

    assert await cur.fetchall() == []

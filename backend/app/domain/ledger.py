"""Double-entry ledger for money (GHS, integer pesewas) and points (PTS).

The database enforces the invariants as a backstop (balanced transactions,
no overdraws, append-only). This module validates postings first so mistakes
fail with a clear error before reaching SQL.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from psycopg import errors

from app.db import Conn


class Unit(StrEnum):
    GHS = "GHS"
    PTS = "PTS"


class Side(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass(frozen=True)
class AccountType:
    prefix: str
    unit: Unit
    normal_side: Side
    allow_negative: bool = False
    # Owned accounts exist once per pickup, rider or household: "<prefix>:<uuid>".
    owned: bool = False


# May go below zero: a refund returns the full amount but the provider keeps its fee.
PROVIDER_CLEARING = AccountType("provider_clearing", Unit.GHS, Side.DEBIT, allow_negative=True)
PROVIDER_FEE_EXPENSE = AccountType("provider_fee_expense", Unit.GHS, Side.DEBIT)
ESCROW = AccountType("escrow:pickup", Unit.GHS, Side.CREDIT, owned=True)
RIDER_PAYABLE = AccountType("rider_payable", Unit.GHS, Side.CREDIT, owned=True)
REFUND_PAYABLE = AccountType("refund_payable", Unit.GHS, Side.CREDIT, owned=True)
PLATFORM_REVENUE = AccountType("platform_revenue", Unit.GHS, Side.CREDIT)
PLATFORM_ADJUSTMENTS = AccountType(
    "platform_adjustments", Unit.GHS, Side.DEBIT, allow_negative=True
)

POINTS_ISSUED = AccountType("points_issued", Unit.PTS, Side.DEBIT)
POINTS_PENDING = AccountType("points_pending", Unit.PTS, Side.CREDIT, owned=True)
POINTS_AVAILABLE = AccountType("points_available", Unit.PTS, Side.CREDIT, owned=True)
POINTS_RECEIVABLE = AccountType("points_receivable", Unit.PTS, Side.DEBIT, owned=True)
POINTS_REDEEMED = AccountType("points_redeemed", Unit.PTS, Side.CREDIT)


@dataclass(frozen=True)
class Account:
    type: AccountType
    owner: UUID | None = None

    def __post_init__(self) -> None:
        if self.type.owned != (self.owner is not None):
            need = "needs an owner" if self.type.owned else "takes no owner"
            raise ValueError(f"account type {self.type.prefix} {need}")

    @property
    def code(self) -> str:
        return f"{self.type.prefix}:{self.owner}" if self.owner else self.type.prefix


@dataclass(frozen=True)
class Leg:
    account: Account
    side: Side
    amount: int


def debit(account: Account, amount: int) -> Leg:
    return Leg(account, Side.DEBIT, amount)


def credit(account: Account, amount: int) -> Leg:
    return Leg(account, Side.CREDIT, amount)


@dataclass(frozen=True)
class Posting:
    kind: str
    idempotency_key: str
    legs: tuple[Leg, ...]
    pickup_id: UUID | None = None
    reverses_txn_id: int | None = None
    memo: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            raise ValueError("posting needs an idempotency key")
        if len(self.legs) < 2:
            raise ValueError("posting needs at least two legs")
        for leg in self.legs:
            if type(leg.amount) is not int or leg.amount <= 0:
                raise ValueError(f"leg amount must be a positive integer, got {leg.amount!r}")
        units = {leg.account.type.unit for leg in self.legs}
        if len(units) != 1:
            raise ValueError(f"posting mixes units: {sorted(units)}")
        codes = [leg.account.code for leg in self.legs]
        if len(codes) != len(set(codes)):
            raise ValueError("posting uses an account more than once")
        debits = sum(leg.amount for leg in self.legs if leg.side is Side.DEBIT)
        credits = sum(leg.amount for leg in self.legs if leg.side is Side.CREDIT)
        if debits != credits:
            raise ValueError(f"posting is unbalanced: debits {debits}, credits {credits}")

    @property
    def unit(self) -> Unit:
        return self.legs[0].account.type.unit

    def signature(self) -> tuple:
        return (
            self.kind,
            self.unit,
            self.pickup_id,
            self.reverses_txn_id,
            tuple(sorted((leg.account.code, leg.side, leg.amount) for leg in self.legs)),
        )


@dataclass(frozen=True)
class PostResult:
    txn_id: int
    replayed: bool


class LedgerError(Exception):
    pass


class InsufficientBalance(LedgerError):
    pass


class IdempotencyConflict(LedgerError):
    pass


async def post(conn: Conn, posting: Posting) -> PostResult:
    """Record a posting once. Replaying the same idempotency key with the same
    posting returns the original transaction. A different posting raises.

    Account rows stay locked until the surrounding transaction commits: post
    last in a transaction, and never hold one open across an external call.
    """
    async with conn.transaction():
        cur = await conn.execute(
            """
            insert into engine.ledger_transactions
                (kind, unit, idempotency_key, pickup_id, reverses_txn_id, memo)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do nothing
            returning id
            """,
            (
                posting.kind,
                posting.unit,
                posting.idempotency_key,
                posting.pickup_id,
                posting.reverses_txn_id,
                posting.memo,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            return await _replay(conn, posting)

        txn_id = row["id"]
        account_ids = await _ensure_accounts(conn, posting.legs)
        # Fixed lock order across postings, so two of them can't deadlock.
        legs = sorted(posting.legs, key=lambda leg: leg.account.code)
        try:
            async with conn.cursor() as entries:
                await entries.executemany(
                    """
                    insert into engine.ledger_entries (txn_id, account_id, side, amount)
                    values (%s, %s, %s, %s)
                    """,
                    [(txn_id, account_ids[leg.account.code], leg.side, leg.amount) for leg in legs],
                )
        except errors.CheckViolation as exc:
            if exc.diag.constraint_name == "ledger_accounts_balance_non_negative":
                raise InsufficientBalance(
                    f"{posting.kind} {posting.idempotency_key}: {exc.diag.message_detail}"
                ) from exc
            raise
        return PostResult(txn_id, replayed=False)


async def balance(conn: Conn, account: Account) -> int:
    cur = await conn.execute(
        "select balance from engine.ledger_accounts where code = %s", (account.code,)
    )
    row = await cur.fetchone()
    return row["balance"] if row else 0


async def _ensure_accounts(conn: Conn, legs: tuple[Leg, ...]) -> dict[str, int]:
    # Sorted, like the entries, so concurrent postings create accounts in one order.
    types = dict(
        sorted(((leg.account.code, leg.account.type) for leg in legs), key=lambda kv: kv[0])
    )
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            insert into engine.ledger_accounts (code, unit, normal_side, allow_negative)
            values (%s, %s, %s, %s)
            on conflict (code) do nothing
            """,
            [(code, t.unit, t.normal_side, t.allow_negative) for code, t in types.items()],
        )
    cur = await conn.execute(
        """
        select id, code, unit, normal_side, allow_negative
          from engine.ledger_accounts where code = any(%s)
        """,
        (list(types),),
    )
    ids = {}
    for row in await cur.fetchall():
        t = types[row["code"]]
        if (row["unit"], row["normal_side"], row["allow_negative"]) != (
            t.unit,
            t.normal_side,
            t.allow_negative,
        ):
            raise LedgerError(f"account {row['code']} in the database does not match its type")
        ids[row["code"]] = row["id"]
    return ids


async def _replay(conn: Conn, posting: Posting) -> PostResult:
    cur = await conn.execute(
        """
        select t.id, t.kind, t.unit, t.pickup_id, t.reverses_txn_id, a.code, e.side, e.amount
          from engine.ledger_transactions t
          join engine.ledger_entries e on e.txn_id = t.id
          join engine.ledger_accounts a on a.id = e.account_id
         where t.idempotency_key = %s
        """,
        (posting.idempotency_key,),
    )
    rows = await cur.fetchall()
    first = rows[0]
    recorded = (
        first["kind"],
        first["unit"],
        first["pickup_id"],
        first["reverses_txn_id"],
        tuple(sorted((r["code"], r["side"], r["amount"]) for r in rows)),
    )
    if recorded != posting.signature():
        raise IdempotencyConflict(
            f"idempotency key {posting.idempotency_key} was already used for a different posting"
        )
    return PostResult(rows[0]["id"], replayed=True)

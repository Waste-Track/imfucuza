from uuid import uuid4

import pytest

from app.domain.ledger import (
    ESCROW,
    PLATFORM_REVENUE,
    POINTS_AVAILABLE,
    PROVIDER_CLEARING,
    Account,
    Posting,
    Unit,
    credit,
    debit,
)

CLEARING = Account(PROVIDER_CLEARING)
REVENUE = Account(PLATFORM_REVENUE)


def escrow() -> Account:
    return Account(ESCROW, uuid4())


def test_balanced_posting_is_accepted():
    posting = Posting("fund_hold", "key-1", (debit(CLEARING, 1000), credit(escrow(), 1000)))

    assert posting.unit is Unit.GHS


def test_owned_account_code_includes_owner():
    owner = uuid4()

    assert Account(ESCROW, owner).code == f"escrow:pickup:{owner}"
    assert CLEARING.code == "provider_clearing"


@pytest.mark.parametrize(
    ("legs", "message"),
    [
        ((debit(CLEARING, 1000), credit(REVENUE, 900)), "unbalanced"),
        ((debit(CLEARING, 1000),), "at least two legs"),
        ((debit(CLEARING, 0), credit(REVENUE, 0)), "positive integer"),
        ((debit(CLEARING, -5), credit(REVENUE, -5)), "positive integer"),
        ((debit(CLEARING, 10.0), credit(REVENUE, 10.0)), "positive integer"),
        ((debit(CLEARING, True), credit(REVENUE, True)), "positive integer"),
        ((debit(CLEARING, 5), credit(CLEARING, 5)), "more than once"),
        ((debit(CLEARING, 5), credit(Account(POINTS_AVAILABLE, uuid4()), 5)), "mixes units"),
    ],
)
def test_invalid_postings_are_rejected(legs, message):
    with pytest.raises(ValueError, match=message):
        Posting("kind", "key", legs)


def test_posting_needs_an_idempotency_key():
    with pytest.raises(ValueError, match="idempotency key"):
        Posting("kind", "", (debit(CLEARING, 1), credit(REVENUE, 1)))


def test_owned_and_unowned_accounts_are_checked():
    with pytest.raises(ValueError, match="needs an owner"):
        Account(ESCROW)
    with pytest.raises(ValueError, match="takes no owner"):
        Account(PROVIDER_CLEARING, uuid4())

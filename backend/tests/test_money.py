from uuid import uuid4

import pytest

from app.domain import money
from app.domain.plastic import earnings


def legs(posting):
    return sorted((leg.account.code.split(":")[0], leg.side, leg.amount) for leg in posting.legs)


def test_fund_hold_books_the_provider_fee_as_our_expense():
    pickup = uuid4()

    posting = money.fund_hold(pickup, "ref", amount=1000, fee=20)

    assert legs(posting) == [
        ("escrow", "credit", 1000),
        ("provider_clearing", "debit", 980),
        ("provider_fee_expense", "debit", 20),
    ]


def test_fund_hold_without_a_fee_has_no_fee_leg():
    assert len(money.fund_hold(uuid4(), "ref", amount=1000, fee=0).legs) == 2


def test_release_splits_escrow_between_rider_and_platform():
    posting = money.release(uuid4(), uuid4(), amount=1000, rider_share_percent=70)

    assert legs(posting) == [
        ("escrow", "debit", 1000),
        ("platform_revenue", "credit", 300),
        ("rider_payable", "credit", 700),
    ]


def test_release_rounds_the_riders_share_down():
    posting = money.release(uuid4(), uuid4(), amount=999, rider_share_percent=70)

    assert ("rider_payable", "credit", 699) in legs(posting)


def test_points_go_to_available_or_pending_and_come_from_points_issued():
    household = uuid4()

    now = money.points_accrue(uuid4(), household, 42, pending=False)
    later = money.points_accrue(uuid4(), household, 42, pending=True)

    assert legs(now) == [("points_available", "credit", 42), ("points_issued", "debit", 42)]
    assert legs(later) == [("points_issued", "debit", 42), ("points_pending", "credit", 42)]


def test_a_supervisor_moves_pending_points_on_or_back():
    household, pickup = uuid4(), uuid4()

    released = money.points_release(pickup, household, 42)
    voided = money.points_void(pickup, household, 42)

    assert legs(released) == [("points_available", "credit", 42), ("points_pending", "debit", 42)]
    assert legs(voided) == [("points_issued", "credit", 42), ("points_pending", "debit", 42)]
    assert {released.idempotency_key, voided.idempotency_key} == {
        f"points-release:{pickup}",
        f"points-void:{pickup}",
    }


def test_rider_pay_for_plastic_is_a_platform_expense():
    assert legs(money.rider_pay(uuid4(), uuid4(), 420)) == [
        ("rider_pay_expense", "debit", 420),
        ("rider_payable", "credit", 420),
    ]


@pytest.mark.parametrize(
    ("declared", "weight_g", "expected"),
    [
        ("pet", 1, (0, 0)),
        ("pet", 999, (9, 99)),
        ("pet", 1000, (10, 100)),
        ("mixed_plastic", 4200, (21, 420)),
        ("hdpe", 25_000, (250, 2500)),
    ],
)
def test_points_and_pay_round_down_to_whole_units(declared, weight_g, expected):
    assert earnings(declared, weight_g) == expected

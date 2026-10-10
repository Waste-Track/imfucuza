from uuid import uuid4

from app.domain import money


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

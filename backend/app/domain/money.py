"""The ledger postings for a paid pickup (engine-design.md section 4)."""

from uuid import UUID

from app.domain.ledger import (
    ESCROW,
    PLATFORM_REVENUE,
    PROVIDER_CLEARING,
    PROVIDER_FEE_EXPENSE,
    REFUND_PAYABLE,
    RIDER_PAYABLE,
    Account,
    Posting,
    credit,
    debit,
)


def escrow(pickup_id: UUID) -> Account:
    return Account(ESCROW, pickup_id)


def fund_hold(pickup_id: UUID, reference: str, amount: int, fee: int) -> Posting:
    """The household's payment arrived. The provider kept its fee, so we hold
    the full amount in escrow and book the fee as our expense."""
    legs = [debit(Account(PROVIDER_CLEARING), amount - fee), credit(escrow(pickup_id), amount)]
    if fee:
        legs.append(debit(Account(PROVIDER_FEE_EXPENSE), fee))
    return Posting("fund_hold", f"pay:{reference}", tuple(legs), pickup_id=pickup_id)


def release(pickup_id: UUID, rider_id: UUID, amount: int, rider_share_percent: int) -> Posting:
    rider_share = amount * rider_share_percent // 100
    legs = [debit(escrow(pickup_id), amount), credit(Account(RIDER_PAYABLE, rider_id), rider_share)]
    if amount - rider_share:
        legs.append(credit(Account(PLATFORM_REVENUE), amount - rider_share))
    return Posting("release", f"release:{pickup_id}", tuple(legs), pickup_id=pickup_id)


def refund_due(pickup_id: UUID, household_id: UUID, amount: int) -> Posting:
    return Posting(
        "refund_due",
        f"refund:{pickup_id}",
        (debit(escrow(pickup_id), amount), credit(Account(REFUND_PAYABLE, household_id), amount)),
        pickup_id=pickup_id,
    )


def refund_extra(pickup_id: UUID, household_id: UUID, reference: str, amount: int) -> Posting:
    """A second payment for an already-funded pickup goes straight back. Keyed
    by its own reference, and outside the one-settlement-per-pickup rule."""
    return Posting(
        "refund_extra",
        f"refund-extra:{reference}",
        (debit(escrow(pickup_id), amount), credit(Account(REFUND_PAYABLE, household_id), amount)),
        pickup_id=pickup_id,
    )


def rider_payout(rider_id: UUID, momo_reference: str, amount: int) -> Posting:
    """A rider was paid by hand over mobile money from our provider balance."""
    return Posting(
        "rider_payout",
        f"payout:{momo_reference}",
        (
            debit(Account(RIDER_PAYABLE, rider_id), amount),
            credit(Account(PROVIDER_CLEARING), amount),
        ),
    )


def refund_paid(pickup_id: UUID, household_id: UUID, reference: str, amount: int) -> Posting:
    """The provider returned the full amount. Its original fee is not returned,
    so clearing can dip below zero: that is money we owe the provider."""
    return Posting(
        "refund_paid",
        f"refund-paid:{reference}",
        (
            debit(Account(REFUND_PAYABLE, household_id), amount),
            credit(Account(PROVIDER_CLEARING), amount),
        ),
        pickup_id=pickup_id,
    )

"""Money edge cases: paying twice, charges that never started, refund outcomes."""

from uuid import UUID, uuid4

from app.adapters.base import (
    ChargeState,
    MomoCharge,
    Network,
    PaymentEventKind,
    ProviderError,
    RefundState,
)
from tests.flow import HOME, offset


def household_id(world, pickup_id: UUID) -> UUID:
    return world.db.one("select household_id from engine.pickup_requests where id = %s", pickup_id)[
        "household_id"
    ]


def second_successful_payment(world, pickup_id: UUID) -> str:
    """A payment that got past the one-pending-prompt guard, for example a
    prompt approved after it was reported failed."""
    reference = f"imf_{uuid4().hex}"
    world.db.run(
        """
        insert into engine.payments
            (pickup_id, direction, provider, reference, network, amount_pesewas)
        values (%s, 'collection', 'fake', %s, 'mtn', 1000)
        """,
        pickup_id,
        reference,
    )
    world.payments.charges[reference] = MomoCharge(
        reference, 1000, "+233200000000", Network.MTN, "x@example.com"
    )
    world.payments.settle(reference)
    world.webhook(PaymentEventKind.CHARGE_SUCCEEDED, reference)
    world.tick()
    return reference


def review_types(world, pickup_id: UUID) -> list[str]:
    return [
        r["type"]
        for r in world.db.all(
            "select type from engine.review_items where pickup_id = %s", pickup_id
        )
    ]


def test_second_prompt_is_refused_while_the_first_is_pending(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    world.client.post(
        f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
    )

    again = world.client.post(
        f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
    )

    assert again.status_code == 409
    assert len(world.payments.charges) == 1


def test_new_prompt_is_allowed_once_the_first_has_failed(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    first = world.client.post(
        f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
    ).json()["reference"]
    world.payments.settle(first, ChargeState.FAILED)

    again = world.client.post(
        f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
    )

    assert again.status_code == 200
    assert (
        world.db.one("select status from engine.payments where reference = %s", first)["status"]
        == "failed"
    )


def test_paying_twice_for_a_live_pickup_refunds_the_extra_and_still_pays_the_rider(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)

    extra = second_successful_payment(world, pickup_id)

    assert world.payments.refunds == [(extra, 1000)]
    assert world.balance(f"escrow:pickup:{pickup_id}") == 1000
    assert "payment_mismatch" in review_types(world, pickup_id)

    pin = world.deliver_and_collect(household, rider, pickup_id)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )
    assert world.status(pickup_id) == "completed"
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_paying_again_after_a_cancelled_refund_refunds_that_payment_too(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)
    world.tick()

    extra = second_successful_payment(world, pickup_id)

    assert [ref for ref, _ in world.payments.refunds][-1] == extra
    assert len(world.payments.refunds) == 2
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert world.balance(f"refund_payable:{household_id(world, pickup_id)}") == 2000


def test_paying_again_after_completion_is_refunded(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    extra = second_successful_payment(world, pickup_id)

    assert world.payments.refunds == [(extra, 1000)]
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_charge_refused_at_start_does_not_block_the_payment_window(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    world.payments.failures["start_momo_charge"] = ProviderError("invalid number", retryable=False)

    started = world.client.post(
        f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
    )
    world.make_due("payment.expire")
    world.tick()

    assert started.status_code == 502
    assert world.status(pickup_id) == "cancelled"


def test_charge_that_timed_out_before_reaching_the_provider_still_expires(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    world.payments.failures["start_momo_charge"] = ProviderError("timed out")

    world.client.post(
        f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
    )
    world.make_due("payment.expire")
    world.tick()

    assert world.status(pickup_id) == "cancelled"
    assert (
        world.db.one("select status from engine.payments where pickup_id = %s", pickup_id)["status"]
        == "failed"
    )


def test_refund_the_provider_refuses_goes_to_a_supervisor(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.payments.failures["refund"] = ProviderError("already reversed", retryable=False)

    world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)
    world.tick()

    assert "payment_mismatch" in review_types(world, pickup_id)
    assert (
        world.db.one("select count(*) as n from engine.jobs where failed_at is not null")["n"] == 0
    )


def test_failed_refund_goes_to_a_supervisor(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)
    world.tick()

    world.refund_settles("refund-1", RefundState.FAILED)

    assert (
        world.db.one(
            "select status from engine.payments where pickup_id = %s and direction = 'refund'",
            pickup_id,
        )["status"]
        == "failed"
    )
    assert "payment_mismatch" in review_types(world, pickup_id)
    assert world.balance(f"refund_payable:{household_id(world, pickup_id)}") == 1000


def test_refund_webhook_is_only_believed_once_the_provider_confirms(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)
    world.tick()
    [(reference, _)] = world.payments.refunds

    # The webhook claims success, but the provider still has it pending.
    world.webhook(PaymentEventKind.REFUND_PROCESSED, reference)
    world.tick()

    assert world.balance(f"refund_payable:{household_id(world, pickup_id)}") == 1000


def test_refund_webhook_for_an_unknown_refund_goes_to_a_supervisor(world):
    reference = f"imf_{uuid4().hex}"

    world.webhook(PaymentEventKind.REFUND_PROCESSED, reference)
    world.tick()

    assert (
        world.db.one(
            """
        select payload->>'reason' as reason from engine.review_items
         where payload->>'reference' = %s
        """,
            reference,
        )["reason"]
        == "refund update for an unknown refund"
    )


def test_each_distinct_money_problem_gets_its_own_review_item(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.payments.failures["refund"] = ProviderError("already reversed", retryable=False)

    second_successful_payment(world, pickup_id)

    reasons = {
        r["reason"]
        for r in world.db.all(
            "select payload->>'reason' as reason from engine.review_items where pickup_id = %s",
            pickup_id,
        )
    }
    assert "paid more than once, refunding" in reasons
    assert any(reason.startswith("refund refused") for reason in reasons)


def test_lost_refund_reply_never_refunds_twice(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)
    world.tick()
    [(reference, _)] = world.payments.refunds
    # The refund went through, but our record of it was lost with the reply.
    world.db.run(
        "update engine.payments set provider_refund_id = null"
        " where direction = 'refund' and reference = %s",
        reference,
    )
    world.db.run(
        "insert into engine.jobs (kind, payload) values ('payment.refund_call', %s)",
        f'{{"reference": "{reference}"}}',
    )

    world.tick()
    world.webhook(PaymentEventKind.REFUND_PROCESSED, reference)
    world.tick()

    assert len(world.payments.refunds) == 1
    assert (
        world.db.one(
            "select count(*) as n from engine.events"
            " where name = 'payment.refund_already_made' and pickup_id = %s",
            pickup_id,
        )["n"]
        == 1
    )
    assert world.balance(f"refund_payable:{household_id(world, pickup_id)}") == 0

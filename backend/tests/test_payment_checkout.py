"""Paying in Paystack's checkout window instead of a direct mobile money prompt."""

from app.adapters.base import ChargeState


def checkout(world, household, pickup_id):
    return world.client.post(f"/v1/pickups/{pickup_id}/checkout", headers=household.headers)


def check(world, household, pickup_id, reference):
    return world.client.post(
        f"/v1/pickups/{pickup_id}/payment/{reference}/check", headers=household.headers
    )


def test_reopening_the_window_resumes_the_same_transaction(world):
    household = world.household()
    pickup_id = world.request_pickup(household)

    first = checkout(world, household, pickup_id).json()
    again = checkout(world, household, pickup_id).json()

    assert first == again
    assert first["access_code"] == f"ac_{first['reference']}"
    [payment] = world.db.all(
        "select network, checkout_code, status from engine.payments where pickup_id = %s",
        pickup_id,
    )
    assert payment == {"network": None, "checkout_code": first["access_code"], "status": "pending"}


def test_a_paid_checkout_moves_the_pickup_on_without_waiting_for_the_webhook(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    reference = checkout(world, household, pickup_id).json()["reference"]
    world.payments.settle(reference)

    checked = check(world, household, pickup_id, reference)

    assert checked.status_code == 200
    assert checked.json()["status"] != "awaiting_payment"
    assert world.balance(f"escrow:pickup:{pickup_id}") == 1000
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_an_unpaid_checkout_leaves_the_pickup_waiting(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    reference = checkout(world, household, pickup_id).json()["reference"]

    assert check(world, household, pickup_id, reference).json() == {"status": "awaiting_payment"}


def test_a_failed_checkout_is_replaced_by_a_new_one(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    first = checkout(world, household, pickup_id).json()["reference"]
    world.payments.settle(first, ChargeState.FAILED)

    second = checkout(world, household, pickup_id).json()["reference"]

    assert second != first


def test_a_paid_pickup_cannot_be_checked_out_again(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)

    response = checkout(world, household, pickup_id)

    assert response.status_code == 409


def test_households_cannot_pay_or_check_each_others_pickups(world):
    owner, other = world.household(), world.household()
    pickup_id = world.request_pickup(owner)
    reference = checkout(world, owner, pickup_id).json()["reference"]

    assert checkout(world, other, pickup_id).status_code == 404
    assert check(world, other, pickup_id, reference).status_code == 404

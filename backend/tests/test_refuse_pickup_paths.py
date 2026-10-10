"""What happens when a refuse pickup doesn't go to plan."""

from app.adapters.base import ChargeState, DeliveryState, PaymentEventKind
from tests.flow import HOME, offset


def _expire(world, kind: str) -> None:
    """Let a timer's moment pass: make its job due, and backdate what it checks."""
    if kind == "offer.expire":
        world.db.run(
            "update engine.dispatch_offers set expires_at = now() - interval '1 second'"
            " where response is null"
        )
    if kind == "pin.expire":
        world.db.run(
            "update engine.pins set expires_at = now() - interval '1 second'"
            " where status in ('active', 'locked')"
        )
    world.make_due(kind)
    world.tick()


# Payment -------------------------------------------------------------------


def test_forged_webhook_is_rejected_and_changes_nothing(world, caplog):
    household = world.household()
    pickup_id = world.request_pickup(household)
    reference = world.pay(household, pickup_id, webhook=False)
    events_before = world.db.one("select count(*) as n from engine.events")["n"]

    response = world.client.post(
        "/webhooks/payments/fake",
        content=b'{"id": "x", "kind": "charge_succeeded", "reference": "'
        + reference.encode()
        + b'"}',
        headers={"x-fake-signature": "forged"},
    )
    world.tick()

    assert response.status_code == 401
    assert world.status(pickup_id) == "awaiting_payment"
    assert world.db.one("select count(*) as n from engine.events")["n"] == events_before
    assert "webhook.rejected" in caplog.text


def test_lost_webhook_is_caught_when_the_payment_window_ends(world):
    household = world.household()
    world.rider(offset(HOME, 200))
    pickup_id = world.request_pickup(household)
    world.pay(household, pickup_id, webhook=False)

    _expire(world, "payment.expire")

    assert world.status(pickup_id) == "offered"
    assert world.balance(f"escrow:pickup:{pickup_id}") == 1000


def test_unpaid_pickup_is_cancelled_when_the_window_ends(world):
    household = world.household()
    pickup_id = world.request_pickup(household)

    _expire(world, "payment.expire")

    assert world.status(pickup_id) == "cancelled"


def test_payment_with_the_wrong_amount_goes_to_a_supervisor(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    reference = world.pay(household, pickup_id, webhook=False)
    charge = world.payments.charges[reference]
    world.payments.charges[reference] = type(charge)(
        charge.reference, 1, charge.phone_e164, charge.network, charge.email
    )
    world.webhook(PaymentEventKind.CHARGE_SUCCEEDED, reference)

    world.tick()

    assert world.status(pickup_id) == "awaiting_payment"
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert (
        world.db.one("select type from engine.review_items where pickup_id = %s", pickup_id)["type"]
        == "payment_mismatch"
    )


def test_failed_charge_leaves_the_pickup_waiting_for_another_try(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    reference = world.pay(household, pickup_id, webhook=False)
    world.payments.settle(reference, ChargeState.FAILED)
    world.webhook(PaymentEventKind.CHARGE_FAILED, reference)

    world.tick()

    assert world.status(pickup_id) == "awaiting_payment"
    assert (
        world.db.one("select status from engine.payments where reference = %s", reference)["status"]
        == "failed"
    )


# Cancellation and refunds ----------------------------------------------------


def test_cancelling_a_paid_pickup_refunds_the_household_in_full(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)

    response = world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)
    world.tick()
    [(_, amount)] = world.payments.refunds
    world.refund_settles("refund-1")

    assert response.json() == {"status": "cancelled", "refund_started": True}
    assert amount == 1000
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    household_id = world.db.one(
        "select household_id from engine.pickup_requests where id = %s", pickup_id
    )["household_id"]
    assert world.balance(f"refund_payable:{household_id}") == 0
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_household_cannot_cancel_once_the_rider_has_arrived(world):
    household = world.household()
    rider = world.rider(offset(HOME, 200))
    pickup_id = world.paid_pickup(household)
    world.deliver_and_collect(household, rider, pickup_id)

    response = world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)

    assert response.status_code == 409
    assert world.payments.refunds == []


def test_money_arriving_after_cancellation_is_refunded(world):
    household = world.household()
    pickup_id = world.request_pickup(household)
    reference = world.pay(household, pickup_id, webhook=False)
    world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=household.headers)

    world.webhook(PaymentEventKind.CHARGE_SUCCEEDED, reference)
    world.tick()

    assert world.status(pickup_id) == "cancelled"
    assert world.payments.refunds == [(reference, 1000)]


# Dispatch --------------------------------------------------------------------


def test_nearest_rider_gets_the_offer(world):
    household = world.household()
    far = world.rider(offset(HOME, 2000))
    near = world.rider(offset(HOME, 300))

    world.paid_pickup(household)

    assert world.open_offer(near)
    assert world.client.get("/v1/riders/me/offers", headers=far.headers).json() == []


def test_expired_offer_moves_to_the_next_rider(world):
    household = world.household()
    first = world.rider(offset(HOME, 300))
    second = world.rider(offset(HOME, 900))
    pickup_id = world.paid_pickup(household)
    world.open_offer(first)

    _expire(world, "offer.expire")

    assert world.status(pickup_id) == "offered"
    assert world.open_offer(second)
    assert (
        world.db.one(
            "select missed_offers from engine.riders r join engine.users u on u.id = r.user_id"
            " where u.phone_e164 = %s",
            first.phone,
        )["missed_offers"]
        == 1
    )


def test_declined_offer_moves_to_the_next_rider(world):
    household = world.household()
    first = world.rider(offset(HOME, 300))
    second = world.rider(offset(HOME, 900))
    world.paid_pickup(household)

    offer = world.open_offer(first)
    world.client.post(f"/v1/offers/{offer['id']}/decline", headers=first.headers)
    world.tick()

    assert world.open_offer(second)


def test_undelivered_offer_sms_marks_the_rider_unreachable_and_moves_on(world):
    household = world.household()
    first = world.rider(offset(HOME, 300), channel="ussd")
    second = world.rider(offset(HOME, 900))
    world.paid_pickup(household)
    for message_id in list(world.sms.states):
        world.sms.states[message_id] = DeliveryState.FAILED

    world.make_due("sms.check_status")
    world.tick()

    assert world.open_offer(second)
    assert world.db.one(
        "select unreachable_until > now() as out from engine.riders r"
        " join engine.users u on u.id = r.user_id where u.phone_e164 = %s",
        first.phone,
    )["out"]


def test_rider_cannot_accept_someone_elses_offer(world):
    household = world.household()
    owner = world.rider(offset(HOME, 300))
    other = world.rider(offset(HOME, 6000))
    world.paid_pickup(household)
    offer = world.open_offer(owner)

    response = world.client.post(f"/v1/offers/{offer['id']}/accept", headers=other.headers)

    assert response.status_code == 404


def test_pickup_nobody_takes_expires_and_is_refunded(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    assert world.status(pickup_id) == "pending_dispatch"

    world.db.run(
        "update engine.pickup_requests set paid_at = now() - interval '2 hours' where id = %s",
        pickup_id,
    )
    _expire(world, "dispatch.next")

    assert world.status(pickup_id) == "expired"
    assert len(world.payments.refunds) == 1


def test_rider_far_from_the_pickup_cannot_mark_arrival(world):
    from datetime import UTC, datetime

    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)

    far = offset(HOME, 800)
    response = world.client.post(
        f"/v1/pickups/{pickup_id}/arrive",
        headers=rider.headers,
        json={
            "lat": far[0],
            "lng": far[1],
            "accuracy_m": 10,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    )

    assert response.status_code == 422
    assert "m from the pickup" in response.json()["detail"]
    assert world.status(pickup_id) == "assigned"


# PIN -------------------------------------------------------------------------


def test_rider_can_enter_the_pin_the_household_reads_out(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)

    response = world.client.post(
        f"/v1/pickups/{pickup_id}/pin", headers=rider.headers, json={"pin": pin}
    )

    assert response.json() == {"status": "completed"}
    assert (
        world.db.one(
            "select confirmed_via from engine.pins where pickup_id = %s and status = 'confirmed'",
            pickup_id,
        )["confirmed_via"]
        == "rider_entry"
    )


def test_five_wrong_pins_lock_it_for_a_supervisor(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    wrong = "111110" if pin != "111110" else "111112"

    results = [
        world.client.post(
            f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": wrong}
        )
        for _ in range(5)
    ]
    sixth = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    assert [r.json()["tries_left"] for r in results] == [4, 3, 2, 1, 0]
    assert results[-1].json()["locked"] is True
    assert sixth.status_code == 423
    assert world.status(pickup_id) == "awaiting_pin"
    assert (
        world.db.one("select type from engine.review_items where pickup_id = %s", pickup_id)["type"]
        == "pin_issue"
    )


def test_unconfirmed_pickup_is_refunded_after_a_day(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.deliver_and_collect(household, rider, pickup_id)

    _expire(world, "pin.expire")

    assert world.status(pickup_id) == "unconfirmed"
    assert len(world.payments.refunds) == 1
    assert (
        world.db.one(
            "select status from engine.pins where pickup_id = %s order by created_at desc limit 1",
            pickup_id,
        )["status"]
        == "expired"
    )


# Access ----------------------------------------------------------------------


def test_unregistered_user_cannot_request_a_pickup(world):
    from tests.flow import Actor

    response = world.client.post(
        "/v1/pickups",
        headers={**Actor().headers, "idempotency-key": "unregistered-key"},
        json={"offering": "refuse"},
    )

    assert response.status_code == 403


def test_pickup_request_needs_an_idempotency_key(world):
    household = world.household()

    response = world.client.post(
        "/v1/pickups", headers=household.headers, json={"offering": "refuse"}
    )

    assert response.status_code == 400


def test_households_only_see_their_own_pickups(world):
    owner, other = world.household(), world.household()
    pickup_id = world.request_pickup(owner)

    response = world.client.get(f"/v1/pickups/{pickup_id}", headers=other.headers)

    assert response.status_code == 404

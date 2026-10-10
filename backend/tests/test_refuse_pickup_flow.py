"""A paid refuse pickup from request to release, through the HTTP API."""

from app.adapters.base import PaymentEventKind
from app.adapters.fakes import FakePaymentProvider
from tests.flow import HOME, offset


def test_paid_refuse_pickup_end_to_end(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))

    # Request, with a retried request returning the same pickup.
    pickup_id = world.request_pickup(household, key="first-request-key")
    assert world.request_pickup(household, key="first-request-key") == pickup_id
    assert world.status(pickup_id) == "awaiting_payment"

    # Pay. A redelivered webhook is dropped.
    reference = world.pay(household, pickup_id, webhook=False)
    body, headers = FakePaymentProvider.webhook(PaymentEventKind.CHARGE_SUCCEEDED, reference)
    first = world.client.post("/webhooks/payments/fake", content=body, headers=headers)
    again = world.client.post("/webhooks/payments/fake", content=body, headers=headers)
    assert (first.json(), again.json()) == ({"status": "queued"}, {"status": "duplicate"})
    world.tick()
    assert world.status(pickup_id) == "offered"
    assert world.balance(f"escrow:pickup:{pickup_id}") == 1000
    assert any("you earn GHS 7.00" in t for t in world.sms.messages_to(rider.phone))

    # Accept, arrive, collect: the household gets a PIN by SMS.
    pin = world.deliver_and_collect(household, rider, pickup_id)
    assert world.status(pickup_id) == "awaiting_pin"

    # A wrong PIN costs a try. The right one completes and releases escrow.
    wrong = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm",
        headers=household.headers,
        json={"pin": "000001" if pin != "000001" else "000002"},
    )
    assert (wrong.status_code, wrong.json()["tries_left"]) == (422, 4)
    right = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )
    assert right.json() == {"status": "completed"}
    assert world.status(pickup_id) == "completed"

    rider_id = world.db.one(
        "select assigned_rider_id from engine.pickup_requests where id = %s", pickup_id
    )["assigned_rider_id"]
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert world.balance(f"rider_payable:{rider_id}") == 700
    assert world.db.all("select * from engine.ledger_reconcile()") == []

    # Every step is in the event log, and the PIN is never stored in plain text.
    names = [
        r["name"]
        for r in world.db.all(
            "select name from engine.events where pickup_id = %s order by id", pickup_id
        )
    ]
    for step in [
        "pickup.requested",
        "pickup.payment_succeeded",
        "pickup.offer_sent",
        "pickup.offer_accepted",
        "pickup.rider_arrived",
        "pickup.refuse_collected",
        "pin.issued",
        "pin.failed",
        "pickup.pin_confirmed",
    ]:
        assert step in names, step
    stored = world.db.one(
        """
        select (select string_agg(body_masked, ' ') from engine.notifications where pickup_id = %s)
            || (select string_agg(payload::text, ' ') from engine.jobs) as text
        """,
        pickup_id,
    )["text"]
    assert pin not in stored

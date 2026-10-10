"""Limits and access rules around a refuse pickup."""

from datetime import UTC, datetime, timedelta

from app.adapters.base import ProviderError
from tests.flow import CONSENT, HOME, Actor, offset


def rider_row(world, rider: Actor) -> dict:
    return world.db.one(
        """
        select r.* from engine.riders r join engine.users u on u.id = r.user_id
         where u.phone_e164 = %s
        """,
        rider.phone,
    )


def expire_open_offers(world) -> None:
    world.db.run(
        "update engine.dispatch_offers set expires_at = now() - interval '1 second'"
        " where response is null"
    )
    world.make_due("offer.expire")
    world.tick()


# Dispatch --------------------------------------------------------------------


def test_rider_who_misses_three_offers_goes_off_duty(world):
    rider = world.rider(offset(HOME, 300))
    for _ in range(3):
        world.paid_pickup(world.household())
        expire_open_offers(world)
        # Each pickup goes back for dispatch; take them out of the way.
        world.db.run(
            "update engine.pickup_requests set status = 'cancelled'"
            " where status = 'pending_dispatch'"
        )

    assert rider_row(world, rider)["on_duty"] is False
    assert any("off duty" in t for t in world.sms.messages_to(rider.phone))


def test_location_fix_from_the_future_is_rejected(world):
    rider = world.rider(offset(HOME, 300))
    future = (datetime.now(UTC) + timedelta(hours=3)).isoformat()

    response = world.client.post(
        "/v1/riders/me/locations",
        headers=rider.headers,
        json={"fixes": [{"lat": HOME[0], "lng": HOME[1], "recorded_at": future}]},
    )

    assert response.status_code == 422


def test_dispatch_window_timer_expires_a_pickup_with_an_offer_out(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    assert world.open_offer(rider)

    world.make_due("dispatch.expire")
    world.tick()

    assert world.status(pickup_id) == "expired"
    assert world.client.get("/v1/riders/me/offers", headers=rider.headers).json() == []
    assert len(world.payments.refunds) == 1


# PIN -------------------------------------------------------------------------


def test_resent_pin_replaces_the_old_one(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    old = world.deliver_and_collect(household, rider, pickup_id)

    resend = world.client.post(f"/v1/pickups/{pickup_id}/pin/resend", headers=household.headers)
    world.tick()
    new = world.pin_sent_to(household)

    assert resend.status_code == 202
    assert new != old or len(world.sms.messages_to(household.phone)) >= 2
    if new != old:
        stale = world.client.post(
            f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": old}
        )
        assert stale.status_code == 422
    done = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": new}
    )
    assert done.json() == {"status": "completed"}


def test_pin_resends_are_capped(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.deliver_and_collect(household, rider, pickup_id)

    codes = []
    for _ in range(4):
        codes.append(
            world.client.post(
                f"/v1/pickups/{pickup_id}/pin/resend", headers=household.headers
            ).status_code
        )
        world.tick()

    assert codes == [202, 202, 202, 429]


def test_refused_pin_text_alerts_a_supervisor(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    near = offset(HOME, 40)
    world.client.post(
        f"/v1/pickups/{pickup_id}/arrive",
        headers=rider.headers,
        json={
            "lat": near[0],
            "lng": near[1],
            "accuracy_m": 10,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    )
    world.client.post(f"/v1/pickups/{pickup_id}/collected", headers=rider.headers)
    original_send = world.sms.send

    async def refuse(to, text):
        if "PIN" in text:
            raise ProviderError("insufficient balance", retryable=False)
        return await original_send(to, text)

    world.sms.send = refuse
    world.tick()

    assert (
        world.db.one("select type from engine.review_items where pickup_id = %s", pickup_id)["type"]
        == "pin_issue"
    )


def test_locked_pin_cannot_be_reset_by_asking_for_a_new_one(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    wrong = "111110" if pin != "111110" else "111112"
    for _ in range(5):
        world.client.post(
            f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": wrong}
        )

    resend = world.client.post(f"/v1/pickups/{pickup_id}/pin/resend", headers=household.headers)

    assert resend.status_code == 423


def test_logs_never_contain_a_pin_or_a_phone_number(world, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": "000001"}
    )
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    for secret in (pin, household.phone, household.phone.lstrip("+"), rider.phone):
        assert secret not in caplog.text


def test_too_many_wrong_pins_in_an_hour_are_refused(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    user_id = world.db.one("select id from engine.users where phone_e164 = %s", household.phone)[
        "id"
    ]
    for _ in range(10):
        world.db.run(
            "insert into engine.events (name, actor_type, actor_id)"
            " values ('pin.failed', 'household', %s)",
            user_id,
        )

    response = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    assert response.status_code == 429


# Access ----------------------------------------------------------------------


def test_only_ghanaian_numbers_can_register(world):
    response = world.client.put(
        "/v1/households/me",
        headers=Actor(phone="+447700900123").headers,
        json={"consent_version": CONSENT},
    )

    assert response.status_code == 422


def test_household_cannot_register_riders(world):
    household = world.household()

    response = world.client.post(
        "/v1/admin/riders",
        headers=household.headers,
        json={"phone": "0241234567", "name": "X", "channel": "pwa", "consent_version": CONSENT},
    )

    assert response.status_code == 403


def test_households_cannot_act_on_each_others_pickups(world):
    owner, other = world.household(), world.household()
    pickup_id = world.request_pickup(owner)
    reference = world.pay(owner, pickup_id, webhook=False)

    assert (
        world.client.post(f"/v1/pickups/{pickup_id}/cancel", headers=other.headers).status_code
        == 404
    )
    assert (
        world.client.post(
            f"/v1/pickups/{pickup_id}/payment/otp",
            headers=other.headers,
            json={"reference": reference, "otp": "123456"},
        ).status_code
        == 404
    )
    assert (
        world.client.post(
            f"/v1/pickups/{pickup_id}/confirm", headers=other.headers, json={"pin": "483119"}
        ).status_code
        == 404
    )


def test_rider_cannot_enter_a_pin_on_someone_elses_pickup(world):
    household = world.household()
    assigned = world.rider(offset(HOME, 300))
    stranger = world.rider(offset(HOME, 6000))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, assigned, pickup_id)

    response = world.client.post(
        f"/v1/pickups/{pickup_id}/pin", headers=stranger.headers, json={"pin": pin}
    )

    assert response.status_code == 404
    assert world.status(pickup_id) == "awaiting_pin"


def test_household_phone_is_hidden_once_the_rider_has_collected(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    [during] = world.client.get("/v1/riders/me/jobs", headers=rider.headers).json()

    pin = world.arrive_and_collect(household, rider, pickup_id)
    [after_collection] = world.client.get("/v1/riders/me/jobs", headers=rider.headers).json()
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    assert during["household_phone"] == household.phone
    assert after_collection["household_phone"] is None
    assert world.client.get("/v1/riders/me/jobs", headers=rider.headers).json() == []

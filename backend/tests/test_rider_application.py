from tests.flow import CONSENT, HOME, Actor, offset

APPLICATION = {
    "consent_version": CONSENT,
    "name": "Kofi Boateng",
    "tricycle_plate": "m-26-gr 1234",
    "work_area": "Madina Estates",
}


def apply(world, rider: Actor, **changes):
    return world.client.put("/v1/riders/me", headers=rider.headers, json={**APPLICATION, **changes})


def approve(world, rider_id: str, active: bool = True):
    return world.client.put(
        f"/v1/admin/riders/{rider_id}/status",
        headers=world.supervisor().headers,
        json={"active": active},
    )


def test_an_application_waits_for_a_supervisor(world):
    rider = Actor()

    response = apply(world, rider)

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"
    me = world.client.get("/v1/me", headers=rider.headers).json()
    assert (me["role"], me["status"]) == ("rider", "pending")
    row = world.db.one(
        """
        select r.tricycle_plate, r.work_area, r.payout_msisdn, u.consent_channel
          from engine.riders r join engine.users u on u.id = r.user_id
         where r.id = %s
        """,
        response.json()["rider_id"],
    )
    assert row["tricycle_plate"] == "M-26-GR 1234"
    assert row["work_area"] == "Madina Estates"
    assert row["payout_msisdn"] == rider.phone
    assert row["consent_channel"] == "pwa"


def test_a_pending_rider_cannot_go_on_duty_or_get_offers(world):
    household = world.household()
    rider = Actor()
    apply(world, rider)

    duty = world.client.put("/v1/riders/me/duty", headers=rider.headers, json={"on_duty": True})
    world.paid_pickup(household)

    assert duty.status_code == 403
    assert world.client.get("/v1/riders/me/offers", headers=rider.headers).status_code == 403


def test_an_approved_rider_is_told_and_can_work(world):
    rider = Actor()
    rider_id = apply(world, rider).json()["rider_id"]

    assert approve(world, rider_id).status_code == 204
    world.tick()

    assert world.client.get("/v1/me", headers=rider.headers).json()["status"] == "active"
    assert any("approved" in text for text in world.sms.messages_to(rider.phone))
    duty = world.client.put("/v1/riders/me/duty", headers=rider.headers, json={"on_duty": True})
    assert duty.status_code == 200


def test_a_turned_down_application_stays_out(world):
    rider = Actor()
    rider_id = apply(world, rider).json()["rider_id"]

    assert approve(world, rider_id, active=False).status_code == 204

    assert world.client.get("/v1/me", headers=rider.headers).json()["status"] == "suspended"


def test_earnings_can_go_to_another_number(world):
    rider = Actor()

    rider_id = apply(world, rider, payout_phone="024 123 4567").json()["rider_id"]

    row = world.db.one("select payout_msisdn from engine.riders where id = %s", rider_id)
    assert row["payout_msisdn"] == "+233241234567"


def test_a_registered_phone_cannot_apply_again(world):
    household = world.household()

    assert apply(world, household).status_code == 409


def test_the_current_consent_notice_is_required(world):
    assert apply(world, Actor(), consent_version="2020-01-v0").status_code == 422


def test_the_household_sees_the_rider_only_while_the_job_is_under_way(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)

    def view():
        return world.client.get(f"/v1/pickups/{pickup_id}", headers=household.headers).json()

    assert view()["rider_phone"] is None
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    assert (view()["rider_name"], view()["rider_phone"]) == ("Kofi", rider.phone)

    pin = world.arrive_and_collect(household, rider, pickup_id)
    world.client.post(f"/v1/pickups/{pickup_id}/pin", headers=rider.headers, json={"pin": pin})

    assert view()["status"] == "completed"
    assert (view()["rider_name"], view()["rider_phone"]) == (None, None)

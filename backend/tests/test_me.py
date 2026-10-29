from tests.flow import CONSENT, HOME, Actor


def test_a_new_phone_has_no_role_yet(world):
    response = world.client.get("/v1/me", headers=Actor().headers)

    assert response.status_code == 200
    body = response.json()
    assert body["role"] is None
    assert body["household_id"] is None
    assert body["consent_version"] == CONSENT
    assert body["refuse_fee_pesewas"] > 0


def test_a_registered_household_is_told_its_role(world):
    household = world.household()

    body = world.client.get("/v1/me", headers=household.headers).json()

    assert body["role"] == "household"
    assert body["user_id"] is not None
    assert body["household_id"] is not None
    assert body["rider_id"] is None


def test_a_rider_signing_in_for_the_first_time_is_linked(world):
    rider = world.rider(HOME)

    body = world.client.get("/v1/me", headers=rider.headers).json()

    assert body["role"] == "rider"
    assert body["name"] == "Kofi"
    assert body["rider_id"] is not None
    assert body["on_duty"] is True

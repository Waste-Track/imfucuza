"""Plastic with a feature-phone rider: the rider weighs by USSD, the household
sends the photo from the app, and the rider vouches for the plastic in hand."""

from uuid import UUID, uuid4

import pytest

from app.adapters.base import PhotoUnreadable, Verdict, VerdictReason
from tests.flow import HOME, Actor, offset, photo


def rider_id(world, rider: Actor) -> UUID:
    return world.db.one(
        "select r.id from engine.riders r join engine.users u on u.id = r.user_id"
        " where u.phone_e164 = %s",
        rider.phone,
    )["id"]


def pickup(world, pickup_id: UUID) -> dict:
    return world.db.one(
        "select status, verification_outcome, weight_g from engine.pickup_requests where id = %s",
        pickup_id,
    )


def points(world, household: Actor) -> dict:
    return world.client.get("/v1/points/me", headers=household.headers).json()


def at_the_door(world) -> tuple[Actor, Actor, UUID]:
    household, rider = world.household(), world.ussd_rider()
    pickup_id = world.plastic_pickup(household)
    assert world.ussd(rider, "3", "1")[-1].startswith("END Accepted")
    world.travel()
    assert world.ussd(rider, "4")[-1].startswith("END Marked as arrived")
    return household, rider, pickup_id


def household_photo(
    world, household: Actor, pickup_id: UUID, weight_g: int = 4200, image: bytes | None = None
):
    return world.client.post(
        f"/v1/pickups/{pickup_id}/plastic/photo",
        params={"weight_g": weight_g},
        headers={**household.headers, "content-type": "image/jpeg"},
        content=photo() if image is None else image,
    )


def weighed(world, typed: str = "4.2") -> tuple[Actor, Actor, UUID]:
    household, rider, pickup_id = at_the_door(world)
    assert world.ussd(rider, "5", typed, "1")[-1].startswith("END Saved")
    world.tick()
    return household, rider, pickup_id


def checked(world, typed: str = "4.2") -> tuple[Actor, Actor, UUID]:
    """Weighed, photographed by the household, and through the photo check."""
    household, rider, pickup_id = weighed(world, typed)
    grams = world.db.one("select weight_g from engine.pickup_requests where id = %s", pickup_id)
    assert household_photo(world, household, pickup_id, grams["weight_g"]).status_code == 200
    world.tick()
    return household, rider, pickup_id


# The whole job ------------------------------------------------------------------


def test_a_feature_phone_rider_and_the_households_photo_complete_a_plastic_pickup(world):
    household, rider, pickup_id = at_the_door(world)

    confirm_screen = world.ussd(rider, "5", "4.2")[-1]
    saved = world.ussd(rider, "5", "4.2", "1")[-1]
    world.tick()
    asked = world.sms.messages_to(household.phone)[-1]
    wanted = world.client.get(f"/v1/pickups/{pickup_id}", headers=household.headers).json()
    sent = household_photo(world, household, pickup_id)
    world.tick()
    vouch = world.sms.messages_to(rider.phone)[-1]
    vouched = world.ussd(rider, "5", "1")[-1]
    world.tick()
    confirmed = world.ussd(rider, "6", world.pin_sent_to(household))[-1]

    assert confirm_screen == "CON Save 4.2 kg?\n1 Yes\n2 Change"
    assert saved.startswith("END Saved: 4.2 kg. The household now takes a photo in the app.")
    assert "weighed 4.2 kg of plastic. If that's right, take a photo" in asked
    assert wanted["photo_wanted"] is True
    assert sent.json() == {"status": "verifying"}
    assert world.classifier.requests[-1].capture_source == "household"
    assert "household's photo passed the check" in vouch
    assert "choose 5" in vouch
    assert vouched.startswith("END Thank you. Take the plastic")
    assert confirmed == "END Confirmed. Thank you for using Imfucuza."
    assert points(world, household) == {"available": 42, "pending": 0}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 420


def test_the_rider_turns_down_plastic_that_does_not_match_the_photo(world):
    household, rider, pickup_id = checked(world)

    world.ussd(rider, "5", "2")

    assert pickup(world, pickup_id)["status"] == "rejected"
    override = world.db.one(
        "select is_override from engine.verification_decisions"
        " where pickup_id = %s and stage = 'rider'",
        pickup_id,
    )
    assert override["is_override"] is True
    assert points(world, household) == {"available": 0, "pending": 0}


def test_a_household_photographed_load_over_5_kg_waits_for_a_supervisor(world):
    household, rider, pickup_id = checked(world, "6")

    world.ussd(rider, "5", "1")
    world.tick()

    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert "A supervisor will check the photo" in world.sms.messages_to(rider.phone)[-1]


@pytest.mark.parametrize(("choice", "after"), [("1", "awaiting_pin"), ("2", "rejected")])
def test_a_feature_phone_rider_decides_when_the_check_is_unsure(world, choice, after):
    household, rider, pickup_id = weighed(world)
    world.classifier.answer(Verdict.UNCERTAIN, 0.5, VerdictReason.OK)
    household_photo(world, household, pickup_id)
    world.tick()
    asked = world.sms.messages_to(rider.phone)[-1]

    screens = world.ussd(rider, "5", choice)

    assert "choose 5 and accept or reject it" in asked
    assert screens[-2].startswith("CON Check the plastic in front of you")
    assert pickup(world, pickup_id)["status"] == after


# The household's photo -------------------------------------------------------------


def test_the_household_photo_waits_for_the_weight_and_is_only_for_basic_phone_riders(world):
    household, _, pickup_id = at_the_door(world)
    too_early = household_photo(world, household, pickup_id)

    other_home = world.household()
    app_rider = world.rider(offset(HOME, 300))
    app_pickup = world.plastic_pickup(other_home)
    offer = world.open_offer(app_rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=app_rider.headers)
    world.arrive(app_rider, app_pickup)
    world.db.run("update engine.pickup_requests set weight_g = 4200 where id = %s", app_pickup)
    riders_job = household_photo(world, other_home, app_pickup)
    not_theirs = household_photo(world, other_home, pickup_id)
    missing = household_photo(world, household, uuid4())

    assert too_early.status_code == 409
    assert riders_job.json()["detail"] == "your rider takes the photo for this pickup"
    assert not_theirs.status_code == 404
    assert missing.status_code == 404
    assert world.photos.objects == {}


def test_a_photo_for_a_weight_the_rider_has_since_changed_is_refused_and_deleted(world):
    household, rider, pickup_id = weighed(world, "4.2")
    world.ussd(rider, "5", "9.9", "1")
    world.tick()

    stale = household_photo(world, household, pickup_id, weight_g=4200)

    assert stale.status_code == 409
    assert "now 9.9 kg" in stale.json()["detail"]
    assert world.photos.objects == {}
    assert pickup(world, pickup_id)["status"] == "arrived"


def test_an_unusable_household_photo_is_retaken_by_the_household(world):
    household, rider, pickup_id = weighed(world)
    world.classifier.fail(PhotoUnreadable("dark"))

    household_photo(world, household, pickup_id)
    world.tick()
    again = household_photo(world, household, pickup_id)

    assert "take another in the app" in world.sms.messages_to(household.phone)[-1]
    assert "taking another" in world.sms.messages_to(rider.phone)[-1]
    assert again.json() == {"status": "verifying"}


# Typing the weight -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "screen"),
    [
        ("4,25", "CON Save 4.25 kg?"),
        ("0.04", "CON Save 0.04 kg?"),
        ("abc", "CON Type the weight in kg"),
        ("0", "CON Type the weight in kg"),
        ("-1", "CON Type the weight in kg"),
        ("NaN", "CON Type the weight in kg"),
        ("1e9", "CON Type the weight in kg"),
        ("9e999990", "CON Type the weight in kg"),
        ("4.2555", "CON Type the weight in kg"),
        ("1_000", "CON Type the weight in kg"),
    ],
)
def test_only_plain_kilograms_reach_the_confirm_screen(world, typed, screen):
    _, rider, pickup_id = at_the_door(world)

    shown = world.ussd(rider, "5", typed)[-1]

    assert shown.startswith(screen)
    assert pickup(world, pickup_id)["weight_g"] is None


def test_a_weight_over_the_limit_is_refused_after_confirming(world):
    _, rider, pickup_id = at_the_door(world)

    shown = world.ussd(rider, "5", "30", "1")[-1]

    assert shown == "END The weight must be more than 0 and at most 25 kg. Choose 5 to try again."
    assert pickup(world, pickup_id)["weight_g"] is None


def test_the_weight_can_be_changed_a_few_times_then_not_again(world):
    household, rider, pickup_id = weighed(world, "4.2")
    for typed in ("4.3", "4.4"):
        world.ussd(rider, "5", typed, "1")
    shown_before = world.ussd(rider, "5")[-1]

    fourth = world.ussd(rider, "5", "4.5", "1")[-1]
    world.tick()

    assert shown_before.startswith("CON Saved: 4.4 kg. To change it")
    assert fourth == "END That step isn't possible for this pickup right now."
    assert pickup(world, pickup_id)["weight_g"] == 4400
    assert sum("weighed" in t for t in world.sms.messages_to(household.phone)) == 3


def test_entering_the_weight_keeps_the_arrival_clock_running(world):
    _, rider, pickup_id = at_the_door(world)
    world.db.run(
        "update engine.pickup_requests set arrived_at = now() - interval '30 minutes'"
        " where id = %s",
        pickup_id,
    )

    world.ussd(rider, "5", "4.2", "1")

    check = world.db.one(
        "select j.run_at - p.arrived_at as after_arrival, j.run_at - now() as left"
        "  from engine.jobs j join engine.pickup_requests p"
        "    on p.id = (j.payload->>'pickup_id')::uuid"
        " where j.kind = 'arrival.check' and p.id = %s order by j.id desc limit 1",
        pickup_id,
    )
    assert abs(check["after_arrival"].total_seconds() - 45 * 60) < 5
    assert check["left"].total_seconds() < 20 * 60


# Elsewhere ------------------------------------------------------------------------


def test_while_the_photo_is_checked_the_rider_is_told_to_wait(world):
    household, rider, pickup_id = weighed(world)
    household_photo(world, household, pickup_id)

    assert world.ussd(rider, "5")[-1] == "END The photo is being checked. You will get an SMS."


def test_an_app_rider_is_sent_to_the_app_for_plastic(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.plastic_pickup(household)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    world.arrive(rider, pickup_id)

    assert world.ussd(rider, "5")[-1] == "END Use the app for plastic: it sends the photo."


def test_a_supervisor_can_hand_plastic_to_a_feature_phone_rider(world):
    household = world.household()
    pickup_id = world.plastic_pickup(household)
    rider = world.ussd_rider(ready=False)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, rider))},
    )

    assert response.json() == {"status": "assigned"}


def test_households_can_check_their_points_by_ussd(world):
    household, rider, pickup_id = checked(world)
    world.ussd(rider, "5", "1")
    world.tick()
    world.ussd(rider, "6", world.pin_sent_to(household))

    assert world.ussd(household, "4")[-1] == "END You have 42 points."

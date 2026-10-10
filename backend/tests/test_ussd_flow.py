"""Feature-phone riders and households working a pickup entirely by USSD."""

import logging
from uuid import uuid4

from app.domain.ussd import NOT_REGISTERED
from tests.flow import CONSENT, HOME, USSD_TOKEN, Actor, offset

MARKET_SQUARE = ("1", "1")  # zone 1, landmark 1 in seed.sql, about 100 m from HOME


def ussd_rider(world) -> Actor:
    rider = Actor()
    response = world.client.post(
        "/v1/admin/riders",
        headers=world.supervisor().headers,
        json={"phone": rider.phone, "name": "Yaw", "channel": "ussd", "consent_version": CONSENT},
    )
    assert response.status_code == 201, response.text
    assert world.ussd(rider, "1")[-1].startswith("END You are on duty")
    assert world.ussd(rider, "2", *MARKET_SQUARE)[-1] == (
        "END Location saved: Market square. Update it when you move."
    )
    return rider


def travel(world) -> None:
    """Pretend the ride to the household took long enough."""
    world.db.run(
        "update engine.dispatch_offers set responded_at = now() - interval '20 minutes'"
        " where response = 'accepted'"
    )


def accept_arrive_collect(world, rider: Actor) -> None:
    assert world.ussd(rider, "3", "1")[-1].startswith("END Accepted")
    travel(world)
    assert world.ussd(rider, "4")[-1].startswith("END Marked as arrived")
    assert world.ussd(rider, "5")[-1].startswith("END Collected")
    world.tick()


def fits(screens: list[str]) -> bool:
    return all(len(s.removeprefix("CON ").removeprefix("END ")) <= 160 for s in screens)


def pin_attempts(world, pickup_id) -> int:
    return world.db.one(
        "select attempts from engine.pins where pickup_id = %s and status = 'active'", pickup_id
    )["attempts"]


# The whole job -----------------------------------------------------------------


def test_feature_phone_pickup_end_to_end(world, caplog):
    caplog.set_level(logging.WARNING)
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)

    offer_sms = world.sms.messages_to(rider.phone)[-1]
    assert "Dial" in offer_sms
    assert "choose 3" in offer_sms

    accepted = world.ussd(rider, "3", "1")
    assert accepted[1].startswith("CON Job: refuse pickup")
    world.tick()
    details = world.sms.messages_to(rider.phone)[-1]
    assert household.phone in details
    assert "near Market square" in details
    travel(world)
    world.ussd(rider, "4")
    world.tick()
    assert any("says they have arrived" in t for t in world.sms.messages_to(household.phone))
    world.ussd(rider, "5")
    world.tick()

    confirmed = world.ussd(household, "1", world.pin_sent_to(household))

    assert confirmed[-1] == "END Confirmed. Thank you for using Imfucuza."
    assert world.status(pickup_id) == "completed"
    assert (
        world.db.one(
            "select confirmed_via from engine.pins where pickup_id = %s and status = 'confirmed'",
            pickup_id,
        )["confirmed_via"]
        == "household_ussd"
    )
    assert fits(accepted + confirmed)
    assert "cut to fit" not in caplog.text


def test_job_details_are_stored_with_the_number_masked(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)

    world.ussd(rider, "3", "1")
    world.tick()

    stored = world.db.one(
        "select body_masked from engine.notifications"
        " where pickup_id = %s and template = 'job_details'",
        pickup_id,
    )["body_masked"]
    assert household.phone not in stored
    assert household.phone[:6] in stored
    assert household.phone not in str(world.db.all("select payload from engine.jobs"))


def test_rider_can_enter_the_household_pin_by_ussd(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)
    accept_arrive_collect(world, rider)

    screens = world.ussd(rider, "6", world.pin_sent_to(household))

    assert screens[-1] == "END Confirmed. Thank you for using Imfucuza."
    assert world.status(pickup_id) == "completed"


def test_older_pickup_waiting_for_a_pin_stays_reachable(world):
    first, second = world.household(), world.household(offset(HOME, 50))
    rider = ussd_rider(world)
    first_pickup = world.paid_pickup(first)
    accept_arrive_collect(world, rider)
    world.paid_pickup(second)
    world.ussd(rider, "3", "1")

    screens = world.ussd(rider, "6", world.pin_sent_to(first))

    assert screens[-1] == "END Confirmed. Thank you for using Imfucuza."
    assert world.status(first_pickup) == "completed"


# PIN -------------------------------------------------------------------------


def test_wrong_pin_by_ussd_counts_as_a_try(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)
    accept_arrive_collect(world, rider)
    pin = world.pin_sent_to(household)

    screens = world.ussd(household, "1", "111110" if pin != "111110" else "111112")

    assert screens[-1] == "END Wrong PIN. 4 tries left. Dial again to retry."
    assert pin_attempts(world, pickup_id) == 1


def test_short_pin_is_asked_again_without_using_a_try(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)
    accept_arrive_collect(world, rider)

    screens = world.ussd(household, "1", "12")

    assert screens[-1].startswith("CON A PIN is 6 digits")
    assert pin_attempts(world, pickup_id) == 0


def test_resent_step_is_answered_again_not_applied_twice(world):
    rider = ussd_rider(world)
    session = {
        "sessionId": f"ATUid_{uuid4().hex}",
        "serviceCode": "*384*123#",
        "networkCode": "62001",
    }
    url = f"/webhooks/ussd/africastalking/{USSD_TOKEN}"
    form = {**session, "phoneNumber": rider.phone}
    before = world.db.one("select count(*) as n from engine.rider_locations")["n"]

    replies = [
        world.client.post(url, data={**form, "text": text}).text
        for text in ["", "2", "2*1", "2*1*1", "2*1*1"]
    ]

    assert replies[-1] == replies[-2]
    assert world.db.one("select count(*) as n from engine.rider_locations")["n"] == before + 1


# Arrival -------------------------------------------------------------------------


def test_arriving_sooner_than_the_trip_allows_is_refused(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)
    world.ussd(rider, "3", "1")

    screens = world.ussd(rider, "4")

    assert screens[-1].startswith("END Arrived already?")
    assert world.status(pickup_id) == "assigned"


def test_arrival_without_collection_is_flagged_for_a_supervisor(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)
    world.ussd(rider, "3", "1")
    travel(world)
    world.ussd(rider, "4")

    world.make_due("arrival.check")
    world.tick()

    assert (
        world.db.one(
            "select payload->>'reason' as reason from engine.review_items where pickup_id = %s",
            pickup_id,
        )["reason"]
        == "arrived but not collected"
    )


def test_app_riders_cannot_self_report_location_or_arrival(world):
    rider = world.rider(offset(HOME, 300))

    assert world.ussd(rider, "2")[-1] == "END Use the app for this: it uses your phone's GPS."
    assert world.ussd(rider, "4")[-1] == "END Use the app for this: it uses your phone's GPS."


def test_location_changes_are_capped(world):
    rider = ussd_rider(world)
    for _ in range(5):
        assert world.ussd(rider, "2", *MARKET_SQUARE)[-1].startswith("END Location saved")

    screens = world.ussd(rider, "2", *MARKET_SQUARE)

    assert screens[-1].startswith("END You have changed your location too often")


# Household -------------------------------------------------------------------


def test_reporting_a_problem_flags_it_without_freezing_the_pickup(world):
    household = world.household()
    rider = ussd_rider(world)
    pickup_id = world.paid_pickup(household)
    accept_arrive_collect(world, rider)

    screens = world.ussd(household, "2", "1")

    assert screens[1].startswith("CON Report a problem")
    assert screens[-1].startswith("END Reported. Don't give your PIN")
    assert world.status(pickup_id) == "awaiting_pin"
    assert (
        world.db.one("select type from engine.review_items where pickup_id = %s", pickup_id)["type"]
        == "household_complaint"
    )


def test_backing_out_of_a_report_reports_nothing(world):
    household = world.household()
    pickup_id = world.request_pickup(household)

    screens = world.ussd(household, "2", "0")

    assert screens[-1].startswith("CON Imfucuza")
    assert world.db.all("select * from engine.review_items where pickup_id = %s", pickup_id) == []


def test_household_can_check_its_last_pickup(world):
    household = world.household()
    world.request_pickup(household)

    assert world.ussd(household, "3")[-1].endswith(": waiting for payment.")


# Menus and sessions ----------------------------------------------------------


def test_rider_with_no_offer_is_told_so(world):
    rider = ussd_rider(world)

    assert world.ussd(rider, "3")[-1] == "END No job offer right now."


def test_invalid_choice_shows_the_menu_again(world):
    assert world.ussd(world.household(), "9")[-1].startswith("CON Choose 1, 2 or 3.")


def test_long_area_lists_are_paged_and_never_cut(world, caplog):
    caplog.set_level(logging.WARNING)
    rider = ussd_rider(world)
    names = [f"Ashaley Botwe New Town {i}" for i in range(12)]
    for i, name in enumerate(names):
        world.db.run(
            "insert into engine.zones (name, lat, lng, radius_m, ussd_index)"
            " values (%s, 5.69, -0.17, 500, %s)",
            name,
            50 + i,
        )
    try:
        first = world.ussd(rider, "2")
        second = world.ussd(rider, "2", "9")
    finally:
        world.db.run("delete from engine.zones where ussd_index >= 50")

    assert first[-1].splitlines()[-2:] == ["9 More", "0 Back"]
    assert second[-1] != first[-1]
    assert fits(first + second)
    assert "cut to fit" not in caplog.text


def test_unregistered_number_is_turned_away(world):
    assert world.ussd(Actor()) == ["END " + NOT_REGISTERED]


def test_session_cannot_be_continued_from_another_phone(world):
    household, intruder = world.household(), world.household()
    session = {
        "sessionId": f"ATUid_{uuid4().hex}",
        "serviceCode": "*384*123#",
        "networkCode": "62001",
    }
    url = f"/webhooks/ussd/africastalking/{USSD_TOKEN}"

    world.client.post(url, data={**session, "phoneNumber": household.phone, "text": ""})
    response = world.client.post(url, data={**session, "phoneNumber": intruder.phone, "text": "3"})

    assert response.text.startswith("END This session belongs to another phone")


def test_idle_session_starts_again_from_the_menu(world):
    household = world.household()
    session = {
        "sessionId": f"ATUid_{uuid4().hex}",
        "serviceCode": "*384*123#",
        "networkCode": "62001",
    }
    url = f"/webhooks/ussd/africastalking/{USSD_TOKEN}"
    form = {**session, "phoneNumber": household.phone}
    world.client.post(url, data={**form, "text": ""})
    world.db.run(
        "update engine.ussd_sessions set updated_at = now() - interval '10 minutes'"
        " where session_id = %s",
        session["sessionId"],
    )

    response = world.client.post(url, data={**form, "text": "3"})

    assert response.text.startswith("CON Imfucuza")


# The route -------------------------------------------------------------------


def test_wrong_token_or_provider_looks_like_no_route(world):
    form = {"sessionId": "s", "serviceCode": "*1#", "phoneNumber": "+233241234567", "text": ""}

    assert world.client.post("/webhooks/ussd/africastalking/wrong", data=form).status_code == 404
    # A provider that exists but isn't the one configured is refused too.
    assert (
        world.client.post(
            f"/webhooks/ussd/arkesel/{USSD_TOKEN}", json={"sessionID": "s"}
        ).status_code
        == 404
    )


def test_ussd_is_off_until_a_token_is_set(world, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("USSD_WEBHOOK_TOKEN", "")
    get_settings.cache_clear()
    form = {"sessionId": "s", "serviceCode": "*1#", "phoneNumber": "+233241234567", "text": ""}

    assert world.client.post("/webhooks/ussd/africastalking/", data=form).status_code == 404


def test_malformed_ussd_request_is_rejected(world):
    response = world.client.post(f"/webhooks/ussd/africastalking/{USSD_TOKEN}", data={"text": "1"})

    assert response.status_code == 400


def test_the_token_is_kept_out_of_access_logs(world):
    from app.main import _RedactUssdToken

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "POST", f"/webhooks/ussd/africastalking/{USSD_TOKEN}", "1.1", 200),
        None,
    )

    _RedactUssdToken().filter(record)

    assert USSD_TOKEN not in record.getMessage()

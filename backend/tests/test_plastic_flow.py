"""Plastic collection end to end with an app rider: request, the check at the
door, the household's PIN, then points and the rider's pay."""

from io import BytesIO
from uuid import UUID, uuid4

import pytest
from PIL import Image

from app.adapters.base import (
    ClassifierUnavailable,
    PhotoUnreadable,
    ProviderError,
    Verdict,
    VerdictReason,
)
from app.config import get_settings
from app.domain import pickups, plastic
from tests.flow import CONSENT, HOME, Actor, offset, photo


def rider_id(world, rider: Actor) -> UUID:
    return world.db.one(
        "select r.id from engine.riders r join engine.users u on u.id = r.user_id"
        " where u.phone_e164 = %s",
        rider.phone,
    )["id"]


def points(world, household: Actor) -> dict:
    return world.client.get("/v1/points/me", headers=household.headers).json()


def pickup(world, pickup_id: UUID) -> dict:
    return world.db.one(
        "select status, verification_outcome, weight_g, points_due, rider_pay_pesewas,"
        " failure_reason from engine.pickup_requests where id = %s",
        pickup_id,
    )


def confirm(world, household: Actor, pickup_id: UUID):
    return world.client.post(
        f"/v1/pickups/{pickup_id}/confirm",
        headers=household.headers,
        json={"pin": world.pin_sent_to(household)},
    )


def stages(world, pickup_id: UUID) -> list[tuple[str, str]]:
    rows = world.db.all(
        "select stage, decision from engine.verification_decisions where pickup_id = %s"
        " order by id",
        pickup_id,
    )
    return [(r["stage"], r["decision"]) for r in rows]


def started(world, declared_type: str = "pet") -> tuple[Actor, Actor, UUID]:
    household, rider = world.household(), world.rider(offset(HOME, 300))
    return household, rider, world.plastic_pickup(household, declared_type)


def decide(world, supervisor: Actor, pickup_id: UUID, accept: bool):
    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/verification",
        headers=supervisor.headers,
        json={"accept": accept, "note": "Checked the photo"},
    )
    world.tick()
    return response


# The happy path ------------------------------------------------------------------


def test_accepted_plastic_earns_points_and_rider_pay_once_the_household_confirms(world):
    household, rider, pickup_id = started(world)

    world.weigh_at_the_door(rider, pickup_id, weight_g=4200)
    before_pin = points(world, household)
    done = confirm(world, household, pickup_id)

    assert done.json() == {"status": "completed"}
    assert before_pin == {"available": 0, "pending": 0}
    assert points(world, household) == {"available": 42, "pending": 0}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 420
    assert "4.2 kg of plastic, worth 42 points" in world.sms.messages_to(household.phone)[-1]
    assert stages(world, pickup_id) == [("classifier", "accept"), ("policy", "accept")]
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_the_classifier_sees_the_photo_and_type_but_nothing_personal(world):
    household, rider, pickup_id = started(world, "hdpe")

    world.weigh_at_the_door(rider, pickup_id)

    [sent] = world.classifier.requests
    stored = world.db.one("select * from engine.pickup_photos where pickup_id = %s", pickup_id)
    assert set(vars(sent)) == {
        "verification_id",
        "pickup_id",
        "declared_type",
        "photo_url",
        "photo_sha256",
        "captured_at",
        "capture_source",
    }
    assert sent.declared_type == "hdpe"
    assert sent.capture_source == "rider"
    assert sent.photo_sha256 == stored["sha256"]
    assert household.phone not in str(vars(sent))
    assert Image.open(BytesIO(world.photos.objects[stored["storage_key"]][0])).format == "JPEG"


def test_mixed_plastic_earns_half_the_points(world):
    household, rider, pickup_id = started(world, "mixed_plastic")

    world.weigh_at_the_door(rider, pickup_id, weight_g=4200)
    confirm(world, household, pickup_id)

    assert points(world, household)["available"] == 21
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 420


def test_rider_pay_for_plastic_waits_out_the_dispute_window(world):
    household, rider, pickup_id = started(world)
    world.weigh_at_the_door(rider, pickup_id)
    confirm(world, household, pickup_id)

    payable = world.client.get(
        f"/v1/admin/riders/{rider_id(world, rider)}/payable", headers=world.supervisor().headers
    ).json()

    assert payable == {"payable_pesewas": 0}


# Requests and dispatch -----------------------------------------------------------


def test_plastic_request_needs_a_type_and_an_eligible_household(world):
    household = world.household()
    untyped = world.client.post(
        "/v1/pickups",
        headers={**household.headers, "idempotency-key": f"req-{uuid4()}"},
        json={"offering": "plastic"},
    )
    world.db.run(
        "update engine.households set plastic_eligible = false"
        " where user_id = (select id from engine.users where phone_e164 = %s)",
        household.phone,
    )
    paused = world.client.post(
        "/v1/pickups",
        headers={**household.headers, "idempotency-key": f"req-{uuid4()}"},
        json={"offering": "plastic", "declared_type": "pp"},
    )

    assert untyped.status_code == 422
    assert paused.status_code == 403


def test_plastic_needs_no_payment_and_is_offered_to_app_riders_only(world):
    household = world.household()
    feature_phone = Actor()
    world.client.post(
        "/v1/admin/riders",
        headers=world.supervisor().headers,
        json={
            "phone": feature_phone.phone,
            "name": "Yaw",
            "channel": "ussd",
            "consent_version": CONSENT,
        },
    )
    world.ussd(feature_phone, "1")
    near = offset(HOME, 100)
    world.db.run(
        "insert into engine.rider_locations (rider_id, source, lat, lng, reported_at)"
        " values (%s, 'landmark', %s, %s, now())",
        rider_id(world, feature_phone),
        *near,
    )
    app_rider = world.rider(offset(HOME, 900))

    pickup_id = world.plastic_pickup(household)

    assert world.open_offer(app_rider)["pickup_id"] == str(pickup_id)
    assert world.open_offer(app_rider)["pay_per_kg_pesewas"] == 100
    assert "you earn GHS 1.00 per kg" in world.sms.messages_to(app_rider.phone)[-1]
    assert (
        world.db.one(
            "select count(*) as n from engine.dispatch_offers where rider_id = %s",
            rider_id(world, feature_phone),
        )["n"]
        == 0
    )


# The policy's outcomes -------------------------------------------------------------


def test_rejected_plastic_earns_nothing_and_the_household_hears_why(world):
    household, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.REJECT, 0.92, VerdictReason.CONTAMINATED)

    world.weigh_at_the_door(rider, pickup_id)

    assert pickup(world, pickup_id)["status"] == "rejected"
    assert "mixed with other waste" in world.sms.messages_to(household.phone)[-1]
    assert not any("PIN" in t for t in world.sms.messages_to(household.phone))
    assert points(world, household) == {"available": 0, "pending": 0}


def test_an_unsure_check_lets_the_rider_decide(world):
    household, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.UNCERTAIN, 0.6, VerdictReason.OK)
    world.weigh_at_the_door(rider, pickup_id)
    asked = world.sms.messages_to(rider.phone)[-1]

    decided = world.client.post(
        f"/v1/pickups/{pickup_id}/rider-decision", headers=rider.headers, json={"accept": True}
    )
    world.tick()
    confirm(world, household, pickup_id)

    assert "accept or reject it in the app" in asked
    assert decided.json() == {"status": "awaiting_pin"}
    assert points(world, household)["available"] == 42
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 420


def test_a_rider_overriding_a_leaning_to_reject_waits_for_a_supervisor(world):
    household, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.REJECT, 0.6, VerdictReason.CONTAMINATED)
    world.weigh_at_the_door(rider, pickup_id)

    decided = world.client.post(
        f"/v1/pickups/{pickup_id}/rider-decision", headers=rider.headers, json={"accept": True}
    )
    world.tick()
    confirm(world, household, pickup_id)

    assert decided.json() == {"status": "awaiting_pin"}
    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert points(world, household) == {"available": 0, "pending": 42}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    override = world.db.one(
        "select is_override from engine.verification_decisions"
        " where pickup_id = %s and stage = 'rider'",
        pickup_id,
    )
    assert override["is_override"] is True


def test_the_rider_can_turn_unsure_plastic_down(world):
    household, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.UNCERTAIN, 0.5, VerdictReason.WRONG_TYPE)
    world.weigh_at_the_door(rider, pickup_id)

    decided = world.client.post(
        f"/v1/pickups/{pickup_id}/rider-decision", headers=rider.headers, json={"accept": False}
    )
    world.tick()

    assert decided.json() == {"status": "rejected"}
    assert "your rider checked it" in world.sms.messages_to(household.phone)[-1]


def test_only_the_assigned_rider_decides_and_only_when_asked(world):
    _, rider, pickup_id = started(world)
    world.weigh_at_the_door(rider, pickup_id)
    stranger = world.rider(offset(HOME, 600))

    not_asked = world.client.post(
        f"/v1/pickups/{pickup_id}/rider-decision", headers=rider.headers, json={"accept": True}
    )
    not_theirs = world.client.post(
        f"/v1/pickups/{pickup_id}/rider-decision", headers=stranger.headers, json={"accept": True}
    )

    assert not_asked.status_code == 409
    assert not_theirs.status_code == 404


# Supervisors ------------------------------------------------------------------------


def to_supervisor(world) -> tuple[Actor, Actor, UUID]:
    household, rider, pickup_id = started(world)
    world.classifier.fail(ClassifierUnavailable("down"))
    world.weigh_at_the_door(rider, pickup_id)
    return household, rider, pickup_id


def test_an_unavailable_classifier_never_decides_on_its_own(world):
    household, _, pickup_id = to_supervisor(world)

    item = world.db.one(
        "select type, payload->>'reason' as reason from engine.review_items"
        " where pickup_id = %s and status = 'open'",
        pickup_id,
    )
    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert pickup(world, pickup_id)["status"] == "awaiting_pin"
    assert item == {"type": "manual_verification", "reason": "the photo checker was unavailable"}
    assert "once a supervisor has checked the photo" in world.sms.messages_to(household.phone)[-1]


def test_points_wait_for_a_supervisor_who_then_releases_them_and_pays_the_rider(world):
    household, rider, pickup_id = to_supervisor(world)
    confirm(world, household, pickup_id)
    pending = points(world, household)
    unpaid = world.balance(f"rider_payable:{rider_id(world, rider)}")

    decided = decide(world, world.supervisor(), pickup_id, accept=True)
    again = decide(world, world.supervisor(), pickup_id, accept=True)

    assert pending == {"available": 0, "pending": 42}
    assert unpaid == 0
    assert decided.json() == {"decision": "accept"}
    assert again.status_code == 409
    assert points(world, household) == {"available": 42, "pending": 0}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 420
    assert (
        world.db.one(
            "select status from engine.review_items where pickup_id = %s"
            " and type = 'manual_verification'",
            pickup_id,
        )["status"]
        == "resolved"
    )
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_a_supervisor_can_void_pending_points(world):
    household, rider, pickup_id = to_supervisor(world)
    confirm(world, household, pickup_id)

    decide(world, world.supervisor(), pickup_id, accept=False)

    assert points(world, household) == {"available": 0, "pending": 0}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    assert "a supervisor checked the photo" in world.sms.messages_to(household.phone)[-1]
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_a_supervisor_rejection_before_the_pin_ends_the_pickup(world):
    household, _, pickup_id = to_supervisor(world)
    pin = world.pin_sent_to(household)

    decide(world, world.supervisor(), pickup_id, accept=False)
    late = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    assert pickup(world, pickup_id)["status"] == "rejected"
    assert late.status_code == 409
    assert points(world, household) == {"available": 0, "pending": 0}


def test_a_supervisor_approval_before_the_pin_pays_in_full_on_confirmation(world):
    household, rider, pickup_id = to_supervisor(world)

    decide(world, world.supervisor(), pickup_id, accept=True)
    confirm(world, household, pickup_id)

    assert points(world, household) == {"available": 42, "pending": 0}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 420


def test_a_rider_who_does_not_decide_hands_it_to_a_supervisor(world):
    _, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.UNCERTAIN, 0.5, VerdictReason.OK)
    world.weigh_at_the_door(rider, pickup_id)

    world.make_due("verification.rider_timeout")
    world.tick()

    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert pickup(world, pickup_id)["status"] == "awaiting_pin"


def test_the_review_item_shows_the_photo_and_every_decision(world):
    _, _, pickup_id = to_supervisor(world)
    supervisor = world.supervisor()
    item_id = world.db.one(
        "select id from engine.review_items where pickup_id = %s and status = 'open'", pickup_id
    )["id"]

    detail = world.client.get(f"/v1/admin/review-items/{item_id}", headers=supervisor.headers)

    [shown] = detail.json()["photos"]
    assert shown["url"].startswith("https://photos.test/")
    assert "storage_key" not in shown
    assert [(d["stage"], d["decision"]) for d in detail.json()["verification"]] == [
        ("classifier", "unavailable")
    ]


# Photos ---------------------------------------------------------------------------


def test_an_unusable_photo_is_retaken_twice_then_goes_to_a_supervisor(world):
    _, rider, pickup_id = started(world)
    for _ in range(3):
        world.classifier.fail(PhotoUnreadable("blurred"))

    world.weigh_at_the_door(rider, pickup_id)
    after_first = pickup(world, pickup_id)["status"]
    world.submit_plastic(rider, pickup_id)
    world.tick()
    world.submit_plastic(rider, pickup_id)
    world.tick()

    assert after_first == "arrived"
    assert "take another" in world.sms.messages_to(rider.phone)[-1]
    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert len(world.classifier.requests) == 3


def test_a_reused_photo_is_rejected_without_asking_the_classifier(world):
    household, rider, first = started(world)
    image = photo()
    world.weigh_at_the_door(rider, first, image=image)
    confirm(world, household, first)
    asked = len(world.classifier.requests)
    second = world.plastic_pickup(household)

    world.weigh_at_the_door(rider, second, image=image)

    assert pickup(world, second)["status"] == "rejected"
    assert pickup(world, second)["failure_reason"] == "duplicate"
    assert len(world.classifier.requests) == asked
    assert "already used for another pickup" in world.sms.messages_to(household.phone)[-1]


def test_a_resized_copy_of_an_earlier_photo_goes_to_a_supervisor(world):
    household, rider, first = started(world)
    image = photo()
    world.weigh_at_the_door(rider, first, image=image)
    confirm(world, household, first)
    out = BytesIO()
    Image.open(BytesIO(image)).resize((540, 480)).save(out, "JPEG", quality=60)
    second = world.plastic_pickup(household)

    world.weigh_at_the_door(rider, second, image=out.getvalue())

    assert pickup(world, second)["verification_outcome"] == "manual_review"
    event = world.db.one(
        "select payload from engine.events where name = 'photo.duplicate' and pickup_id = %s",
        second,
    )
    assert event["payload"]["exact"] is False


@pytest.mark.parametrize(
    ("weight_g", "body", "status"),
    [
        (0, None, 422),
        (25_001, None, 422),
        (4200, b"not a photo", 422),
        (4200, b"\xff\xd8" + b"\x00" * (5 * 1024 * 1024), 413),
    ],
    ids=["no-weight", "too-heavy", "not-a-photo", "too-large"],
)
def test_bad_submissions_are_refused_and_change_nothing(world, weight_g, body, status):
    _, rider, pickup_id = started(world)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    world.arrive(rider, pickup_id)

    response = world.submit_plastic(rider, pickup_id, weight_g, body)

    assert response.status_code == status
    assert pickup(world, pickup_id)["status"] == "arrived"
    assert world.photos.objects == {}


def test_a_photo_is_only_taken_at_the_door_of_a_plastic_pickup(world):
    household, rider, pickup_id = started(world)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    early = world.submit_plastic(rider, pickup_id)
    world.arrive(rider, pickup_id)
    stranger = world.rider(offset(HOME, 600))
    not_theirs = world.submit_plastic(stranger, pickup_id)

    assert early.status_code == 409
    assert not_theirs.status_code == 404


# Limits on free, weight-paid pickups ----------------------------------------------


def test_a_heavy_load_waits_for_a_supervisor_before_anyone_is_paid(world):
    household, rider, pickup_id = started(world)

    world.weigh_at_the_door(rider, pickup_id, weight_g=12_000)
    confirm(world, household, pickup_id)

    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert points(world, household) == {"available": 0, "pending": 120}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    reason = world.db.one(
        "select payload->>'reason' as reason from engine.review_items where pickup_id = %s",
        pickup_id,
    )["reason"]
    assert "heavy load" in reason


def test_a_household_has_one_plastic_pickup_open_at_a_time(world, monkeypatch):
    monkeypatch.setenv("PLASTIC_PICKUPS_PER_DAY", "2")
    get_settings.cache_clear()
    household = world.household()
    first = world.plastic_pickup(household)

    def request():
        return world.client.post(
            "/v1/pickups",
            headers={**household.headers, "idempotency-key": f"req-{uuid4()}"},
            json={"offering": "plastic", "declared_type": "pet"},
        )

    while_open = request()
    world.client.post(f"/v1/pickups/{first}/cancel", headers=household.headers)
    second = request()
    world.client.post(f"/v1/pickups/{second.json()['id']}/cancel", headers=household.headers)
    third = request()

    assert while_open.status_code == 409
    assert second.status_code == 201
    assert third.status_code == 409
    assert "2 times a day" in third.json()["detail"]


# When things go wrong ------------------------------------------------------------


def arrived(world) -> tuple[Actor, Actor, UUID]:
    household, rider, pickup_id = started(world)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    world.arrive(rider, pickup_id)
    return household, rider, pickup_id


def test_a_storage_outage_refuses_the_upload_and_records_nothing(world, monkeypatch):
    _, rider, pickup_id = arrived(world)

    async def down(*args, **kwargs):
        raise ProviderError("storage down")

    monkeypatch.setattr(world.photos, "put", down)
    response = world.submit_plastic(rider, pickup_id)

    assert response.status_code == 503
    assert pickup(world, pickup_id)["status"] == "arrived"
    assert world.db.one(
        "select count(*) as n from engine.pickup_photos where pickup_id = %s", pickup_id
    ) == {"n": 0}


def test_a_photo_whose_record_fails_is_deleted_again(world, monkeypatch):
    _, rider, pickup_id = arrived(world)

    async def clash(*args, **kwargs):
        raise pickups.StaleVersion("someone else got there first")

    monkeypatch.setattr(plastic, "submit", clash)
    response = world.submit_plastic(rider, pickup_id)

    assert response.status_code == 409
    assert world.photos.objects == {}


def test_storage_failing_during_the_check_sends_it_straight_to_a_supervisor(world, monkeypatch):
    _, rider, pickup_id = arrived(world)
    world.submit_plastic(rider, pickup_id)

    async def down(*args, **kwargs):
        raise ProviderError("storage down")

    monkeypatch.setattr(world.photos, "signed_url", down)
    world.tick()

    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"
    assert world.classifier.requests == []


def test_a_check_that_keeps_crashing_ends_with_a_supervisor(world):
    _, rider, pickup_id = arrived(world)
    for _ in range(10):
        world.classifier.fail(RuntimeError("bug"))
    world.submit_plastic(rider, pickup_id)

    for _ in range(6):
        world.make_due("verification.run")
        world.tick()

    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"


def test_a_rider_decision_after_the_timeout_changes_nothing(world):
    _, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.UNCERTAIN, 0.5, VerdictReason.OK)
    world.weigh_at_the_door(rider, pickup_id)
    world.make_due("verification.rider_timeout")
    world.tick()

    late = world.client.post(
        f"/v1/pickups/{pickup_id}/rider-decision", headers=rider.headers, json={"accept": True}
    )

    assert late.status_code == 409
    assert (
        world.db.one(
            "select count(*) as n from engine.verification_decisions"
            " where pickup_id = %s and stage = 'rider'",
            pickup_id,
        )["n"]
        == 0
    )


def test_a_photo_reused_by_another_household_is_rejected(world):
    first_home, rider, first = started(world)
    image = photo()
    world.weigh_at_the_door(rider, first, image=image)
    confirm(world, first_home, first)
    other_home = world.household()
    second = world.plastic_pickup(other_home)

    world.weigh_at_the_door(rider, second, image=image)

    assert pickup(world, second)["failure_reason"] == "duplicate"


def test_an_oversized_upload_is_cut_off_even_without_a_declared_length(world):
    _, rider, pickup_id = arrived(world)

    def chunks():
        for _ in range(6):
            yield b"\xff" * (1024 * 1024)

    response = world.client.post(
        f"/v1/pickups/{pickup_id}/plastic",
        params={"weight_g": 4200},
        headers={**rider.headers, "content-type": "image/jpeg"},
        content=chunks(),
    )

    assert response.status_code == 413
    assert pickup(world, pickup_id)["status"] == "arrived"


def test_rider_pay_becomes_payable_after_the_dispute_window(world, monkeypatch):
    monkeypatch.setenv("PAYOUT_HOLD_S", "0")
    get_settings.cache_clear()
    household, rider, pickup_id = started(world)
    world.weigh_at_the_door(rider, pickup_id)
    confirm(world, household, pickup_id)

    payable = world.client.get(
        f"/v1/admin/riders/{rider_id(world, rider)}/payable", headers=world.supervisor().headers
    ).json()

    assert payable == {"payable_pesewas": 420}


def test_the_rider_hears_when_the_plastic_is_turned_down(world):
    _, rider, pickup_id = started(world)
    world.classifier.answer(Verdict.REJECT, 0.95, VerdictReason.WRONG_TYPE)

    world.weigh_at_the_door(rider, pickup_id)

    assert "Leave it with the household" in world.sms.messages_to(rider.phone)[-1]


# Supervisor guard rails ----------------------------------------------------------


def test_a_manual_check_cannot_be_closed_without_deciding(world):
    _, _, pickup_id = to_supervisor(world)
    supervisor = world.supervisor()
    item_id = world.db.one(
        "select id from engine.review_items where pickup_id = %s and status = 'open'", pickup_id
    )["id"]

    response = world.client.post(
        f"/v1/admin/review-items/{item_id}/resolve",
        headers=supervisor.headers,
        json={"resolution": "Looks fine"},
    )

    assert response.status_code == 409
    assert pickup(world, pickup_id)["verification_outcome"] == "manual_review"


def test_a_check_claimed_by_someone_else_is_theirs_to_decide(world):
    _, _, pickup_id = to_supervisor(world)
    first, second = world.supervisor(), world.supervisor()
    item_id = world.db.one(
        "select id from engine.review_items where pickup_id = %s and status = 'open'", pickup_id
    )["id"]
    world.client.post(f"/v1/admin/review-items/{item_id}/claim", headers=first.headers)

    theirs = decide(world, second, pickup_id, accept=True)
    mine = decide(world, first, pickup_id, accept=True)

    assert theirs.status_code == 409
    assert mine.status_code == 200


def test_plastic_cannot_be_assigned_to_a_feature_phone_rider(world):
    household = world.household()
    pickup_id = world.plastic_pickup(household)
    feature_phone = Actor()
    world.client.post(
        "/v1/admin/riders",
        headers=world.supervisor().headers,
        json={
            "phone": feature_phone.phone,
            "name": "Yaw",
            "channel": "ussd",
            "consent_version": CONSENT,
        },
    )

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, feature_phone))},
    )

    assert response.status_code == 409


def test_an_app_rider_can_still_see_a_plastic_offer_on_ussd(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))
    world.plastic_pickup(household)

    screen = world.ussd(rider, "3")[-1]

    assert "plastic pickup" in screen
    assert "per kg" in screen

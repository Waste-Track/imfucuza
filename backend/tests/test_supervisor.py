"""Supervisors working the review queue, stepping into pickups and paying riders."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.config import get_settings
from tests.flow import CONSENT, HOME, Actor, offset


def rider_id(world, rider: Actor) -> UUID:
    return world.db.one(
        "select r.id from engine.riders r join engine.users u on u.id = r.user_id"
        " where u.phone_e164 = %s",
        rider.phone,
    )["id"]


def ussd_rider(world) -> Actor:
    rider = Actor()
    response = world.client.post(
        "/v1/admin/riders",
        headers=world.supervisor().headers,
        json={"phone": rider.phone, "name": "Yaw", "channel": "ussd", "consent_version": CONSENT},
    )
    assert response.status_code == 201, response.text
    return rider


def lock_the_pin(world, household: Actor, pickup_id: UUID, pin: str) -> None:
    wrong = "111110" if pin != "111110" else "111112"
    for _ in range(5):
        world.client.post(
            f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": wrong}
        )


def arrive(world, rider: Actor, pickup_id: UUID):
    near = offset(HOME, 40)
    return world.client.post(
        f"/v1/pickups/{pickup_id}/arrive",
        headers=rider.headers,
        json={
            "lat": near[0],
            "lng": near[1],
            "accuracy_m": 10,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    )


def open_item(world, pickup_id: UUID) -> dict:
    return world.db.one(
        "select id, type, payload->>'reason' as reason from engine.review_items"
        " where pickup_id = %s and status = 'open'",
        pickup_id,
    )


def complete(world, household: Actor, rider: Actor) -> UUID:
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )
    return pickup_id


def completed_pickup(world) -> tuple[Actor, Actor, UUID]:
    household, rider = world.household(), world.rider(offset(HOME, 300))
    return household, rider, complete(world, household, rider)


def reference() -> str:
    return f"MM{uuid4().hex[:10].upper()}"


def pay(world, supervisor: Actor, rider: Actor, amount: int, momo_reference: str | None = None):
    body: dict = {"amount_pesewas": amount}
    if momo_reference:
        body["momo_reference"] = momo_reference
    return world.client.post(
        f"/v1/admin/riders/{rider_id(world, rider)}/payouts", headers=supervisor.headers, json=body
    )


@pytest.fixture
def no_payout_hold(monkeypatch):
    monkeypatch.setenv("PAYOUT_HOLD_S", "0")
    get_settings.cache_clear()


@pytest.fixture
def approval_above_5_cedis(monkeypatch, no_payout_hold):
    monkeypatch.setenv("PAYOUT_SECOND_APPROVAL_PESEWAS", "500")
    get_settings.cache_clear()


# Access ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/admin/review-items"),
        ("get", "/v1/admin/riders"),
        ("get", "/v1/admin/payouts"),
        ("get", "/v1/admin/zones"),
        ("post", "/v1/admin/payouts/00000000-0000-0000-0000-000000000000/approve"),
    ],
)
def test_only_supervisors_can_use_the_console(world, method, path):
    household = world.household()

    response = getattr(world.client, method)(path, headers=household.headers)

    assert response.status_code == 403


# Review queue ------------------------------------------------------------------


def test_locked_pin_appears_in_the_queue_with_its_full_story(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    lock_the_pin(world, household, pickup_id, pin)
    supervisor = world.supervisor()

    queue = world.client.get("/v1/admin/review-items", headers=supervisor.headers).json()
    item = next(i for i in queue if i["pickup_id"] == str(pickup_id))
    detail = world.client.get(
        f"/v1/admin/review-items/{item['id']}", headers=supervisor.headers
    ).json()

    assert item["type"] == "pin_issue"
    assert detail["pickup"]["status"] == "awaiting_pin"
    assert [p["status"] for p in detail["pins"]] == ["locked"]
    assert any(e["name"] == "pin.locked" for e in detail["timeline"])
    assert "pin_hmac" not in str(detail)
    assert pin not in str(detail)


def test_claimed_item_can_only_be_resolved_by_its_claimer(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.db.run(
        "insert into engine.review_items (type, pickup_id, payload)"
        " values ('household_complaint', %s, '{}')",
        pickup_id,
    )
    item_id = open_item(world, pickup_id)["id"]
    first, second = world.supervisor(), world.supervisor()
    url = f"/v1/admin/review-items/{item_id}"

    assert world.client.post(f"{url}/claim", headers=first.headers).status_code == 204
    assert world.client.post(f"{url}/claim", headers=second.headers).status_code == 409
    not_mine = world.client.post(
        f"{url}/resolve", headers=second.headers, json={"resolution": "not mine"}
    )
    mine = world.client.post(
        f"{url}/resolve", headers=first.headers, json={"resolution": "Called, all fine"}
    )

    assert not_mine.status_code == 409
    assert mine.status_code == 204
    assert (
        world.db.one("select status from engine.review_items where id = %s", item_id)["status"]
        == "resolved"
    )


def test_a_claim_left_for_an_hour_can_be_taken_over(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    world.db.run(
        "insert into engine.review_items (type, pickup_id, payload, assigned_to, claimed_at)"
        " select 'household_complaint', %s, '{}', id, now() - interval '2 hours'"
        "   from engine.users where role = 'supervisor' limit 1",
        pickup_id,
    )
    item_id = open_item(world, pickup_id)["id"]

    response = world.client.post(
        f"/v1/admin/review-items/{item_id}/claim", headers=world.supervisor().headers
    )

    assert response.status_code == 204


def test_stalled_pickup_item_stays_open_until_the_pickup_is_dealt_with(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/offers/{world.open_offer(rider)['id']}/accept", headers=rider.headers)
    assert arrive(world, rider, pickup_id).status_code == 200
    world.make_due("arrival.check")
    world.tick()
    item = open_item(world, pickup_id)
    supervisor = world.supervisor()
    resolve = f"/v1/admin/review-items/{item['id']}/resolve"

    too_early = world.client.post(
        resolve, headers=supervisor.headers, json={"resolution": "Rider says soon"}
    )
    world.client.post(
        f"/v1/admin/pickups/{pickup_id}/cancel",
        headers=supervisor.headers,
        json={"reason": "Rider left without collecting"},
    )
    after_cancel = world.client.post(
        resolve, headers=supervisor.headers, json={"resolution": "Cancelled and refunded"}
    )

    assert item["type"] == "dispatch_stalled"
    assert too_early.status_code == 409
    assert after_cancel.status_code == 204


# Pickups -----------------------------------------------------------------------


def test_supervisor_reissue_clears_a_locked_pin(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    lock_the_pin(world, household, pickup_id, pin)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/reissue-pin", headers=world.supervisor().headers
    )
    world.tick()
    done = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm",
        headers=household.headers,
        json={"pin": world.pin_sent_to(household)},
    )

    assert response.status_code == 202
    assert done.json() == {"status": "completed"}


def test_supervisor_pin_reissues_are_capped(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.deliver_and_collect(household, rider, pickup_id)
    supervisor = world.supervisor()
    url = f"/v1/admin/pickups/{pickup_id}/reissue-pin"

    codes = []
    for _ in range(8):
        codes.append(world.client.post(url, headers=supervisor.headers).status_code)
        world.tick()

    assert codes == [202] * 7 + [429]


def test_double_clicked_reissue_sends_one_pin(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    lock_the_pin(world, household, pickup_id, pin)
    supervisor = world.supervisor()

    for _ in range(2):
        world.client.post(f"/v1/admin/pickups/{pickup_id}/reissue-pin", headers=supervisor.headers)
    world.tick()

    assert (
        world.db.one("select count(*) as n from engine.pins where pickup_id = %s", pickup_id)["n"]
        == 2
    )


def test_supervisor_cancel_after_collection_refunds_and_tells_both_sides(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/cancel",
        headers=world.supervisor().headers,
        json={"reason": "Household reported the rider never came"},
    )
    world.tick()
    late_pin = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )

    assert response.json() == {"status": "cancelled", "refund_started": True}
    assert world.status(pickup_id) == "cancelled"
    assert late_pin.status_code == 409
    assert len(world.payments.refunds) == 1
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    assert world.db.all("select * from engine.ledger_reconcile()") == []
    assert any("supervisor cancelled" in t for t in world.sms.messages_to(household.phone))
    assert any("supervisor cancelled" in t for t in world.sms.messages_to(rider.phone))


def test_completed_pickup_cannot_be_cancelled(world):
    _, rider, pickup_id = completed_pickup(world)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/cancel",
        headers=world.supervisor().headers,
        json={"reason": "Trying to take the money back"},
    )

    assert response.status_code == 409
    assert world.status(pickup_id) == "completed"
    assert world.payments.refunds == []
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 700


def test_no_show_rider_is_replaced_by_redispatch(world):
    household = world.household()
    no_show = world.rider(offset(HOME, 300))
    other = world.rider(offset(HOME, 900))
    pickup_id = world.paid_pickup(household)
    offer = world.open_offer(no_show)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=no_show.headers)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/redispatch", headers=world.supervisor().headers
    )
    world.tick()

    assert response.status_code == 202
    assert world.open_offer(other)
    assert world.client.get("/v1/riders/me/offers", headers=no_show.headers).json() == []
    assert arrive(world, no_show, pickup_id).status_code == 404


def test_redispatch_after_the_dispatch_window_is_refused(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/offers/{world.open_offer(rider)['id']}/accept", headers=rider.headers)
    world.db.run(
        "update engine.pickup_requests set paid_at = now() - interval '2 hours' where id = %s",
        pickup_id,
    )

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/redispatch", headers=world.supervisor().headers
    )

    assert response.status_code == 409
    assert world.status(pickup_id) == "assigned"


def test_feature_phone_rider_assigned_from_afar_gets_details_and_must_travel(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    assert world.status(pickup_id) == "pending_dispatch"
    rider = ussd_rider(world)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, rider))},
    )
    world.tick()

    assert response.json() == {"status": "assigned"}
    assert world.status(pickup_id) == "assigned"
    assert any(household.phone in t for t in world.sms.messages_to(rider.phone))
    assert world.ussd(rider, "4")[-1].startswith("END Arrived already?")


def test_app_rider_assigned_by_a_supervisor_is_not_texted_the_household_number(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    rider = world.rider(offset(HOME, 8000))

    world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, rider))},
    )
    world.tick()

    texts = world.sms.messages_to(rider.phone)
    assert any("Open the app" in t for t in texts)
    assert not any(household.phone in t for t in texts)


def test_no_show_rider_can_be_swapped_for_a_chosen_one(world):
    household = world.household()
    no_show = world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.client.post(
        f"/v1/offers/{world.open_offer(no_show)['id']}/accept", headers=no_show.headers
    )
    chosen = world.rider(offset(HOME, 600))

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, chosen))},
    )
    world.tick()

    assert response.status_code == 200
    assert arrive(world, no_show, pickup_id).status_code == 404
    assert arrive(world, chosen, pickup_id).status_code == 200
    assert any("given to another rider" in t for t in world.sms.messages_to(no_show.phone))


def test_rider_swapped_out_before_the_sms_goes_never_gets_the_household_number(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)
    first, second = ussd_rider(world), ussd_rider(world)
    supervisor = world.supervisor()

    for rider in (first, second):
        world.client.post(
            f"/v1/admin/pickups/{pickup_id}/assign",
            headers=supervisor.headers,
            json={"rider_id": str(rider_id(world, rider))},
        )
    world.tick()

    assert not any(household.phone in t for t in world.sms.messages_to(first.phone))
    assert any(household.phone in t for t in world.sms.messages_to(second.phone))


def test_busy_rider_cannot_be_assigned_another_pickup(world):
    first, second = world.household(), world.household()
    rider = world.rider(offset(HOME, 300))
    world.paid_pickup(first)
    offer = world.open_offer(rider)
    world.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers)
    pickup_id = world.paid_pickup(second)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, rider))},
    )

    assert response.status_code == 409


def test_rider_with_an_open_offer_cannot_be_assigned_another_pickup(world):
    first, second = world.household(), world.household()
    rider = world.rider(offset(HOME, 300))
    world.paid_pickup(first)
    assert world.open_offer(rider)
    pickup_id = world.paid_pickup(second)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=world.supervisor().headers,
        json={"rider_id": str(rider_id(world, rider))},
    )

    assert response.status_code == 409
    assert "offer" in response.json()["detail"]


# Riders ----------------------------------------------------------------------


def suspend(world, rider: Actor):
    return world.client.put(
        f"/v1/admin/riders/{rider_id(world, rider)}/status",
        headers=world.supervisor().headers,
        json={"active": False},
    )


def test_suspended_rider_goes_off_duty_and_gets_no_offers(world):
    household = world.household()
    rider = world.rider(offset(HOME, 300))

    response = suspend(world, rider)
    pickup_id = world.paid_pickup(household)

    assert response.status_code == 204
    assert (
        world.db.one("select on_duty from engine.riders where id = %s", rider_id(world, rider))[
            "on_duty"
        ]
        is False
    )
    assert (
        world.db.one(
            "select count(*) as n from engine.dispatch_offers where pickup_id = %s", pickup_id
        )["n"]
        == 0
    )


def test_suspending_a_rider_passes_their_offer_on_and_flags_their_job(world):
    first, second = world.household(), world.household()
    rider = world.rider(offset(HOME, 300))
    busy_pickup = world.paid_pickup(first)
    world.client.post(f"/v1/offers/{world.open_offer(rider)['id']}/accept", headers=rider.headers)
    world.db.run(
        "update engine.riders set max_active_jobs = 2 where id = %s", rider_id(world, rider)
    )
    offered_pickup = world.paid_pickup(second)
    assert world.open_offer(rider)
    other = world.rider(offset(HOME, 900))

    suspend(world, rider)
    world.tick()

    assert world.status(offered_pickup) == "offered"
    assert world.open_offer(other)["pickup_id"] == str(offered_pickup)
    assert open_item(world, busy_pickup)["reason"] == "rider suspended mid-job"


def test_suspended_riders_job_can_be_closed_once_it_has_a_new_rider(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    world.client.post(f"/v1/offers/{world.open_offer(rider)['id']}/accept", headers=rider.headers)
    suspend(world, rider)
    item_id = open_item(world, pickup_id)["id"]
    supervisor = world.supervisor()
    resolve = f"/v1/admin/review-items/{item_id}/resolve"

    before = world.client.post(resolve, headers=supervisor.headers, json={"resolution": "Later"})
    world.client.post(
        f"/v1/admin/pickups/{pickup_id}/assign",
        headers=supervisor.headers,
        json={"rider_id": str(rider_id(world, world.rider(offset(HOME, 600))))},
    )
    after = world.client.post(resolve, headers=supervisor.headers, json={"resolution": "Moved"})

    assert before.status_code == 409
    assert after.status_code == 204


# Payouts -------------------------------------------------------------------------


def test_earnings_are_held_through_the_dispute_window(world):
    _, rider, _ = completed_pickup(world)

    payable = world.client.get(
        f"/v1/admin/riders/{rider_id(world, rider)}/payable", headers=world.supervisor().headers
    ).json()

    assert payable == {"payable_pesewas": 0}


def test_small_payout_never_exceeds_what_is_owed(world, no_payout_hold):
    _, rider, _ = completed_pickup(world)
    supervisor = world.supervisor()

    too_much = pay(world, supervisor, rider, 701, reference())
    paid = pay(world, supervisor, rider, 700, reference())

    assert too_much.json()["detail"] == "only GHS 7.00 can be paid out now"
    assert paid.json()["status"] == "recorded"
    payout = world.db.one(
        "select destination_msisdn from engine.payouts where id = %s", paid.json()["payout_id"]
    )
    assert payout["destination_msisdn"] == rider.phone
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_one_transfer_cannot_be_recorded_twice(world, no_payout_hold):
    rider = world.rider(offset(HOME, 300))
    complete(world, world.household(), rider)
    complete(world, world.household(), rider)
    supervisor = world.supervisor()
    ref = reference()

    first = pay(world, supervisor, rider, 700, f" {ref[:4].lower()} {ref[4:]}")
    again = pay(world, supervisor, rider, 700, ref)

    assert first.json()["status"] == "recorded"
    assert again.status_code == 409
    assert again.json()["detail"] == "this mobile money reference is already recorded"
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 700


def test_reference_must_look_like_a_mobile_money_reference(world, no_payout_hold):
    _, rider, _ = completed_pickup(world)

    response = pay(world, world.supervisor(), rider, 700, "n/a!")

    assert response.status_code == 422


def test_large_payout_is_approved_by_a_second_supervisor_before_it_is_sent(
    world, approval_above_5_cedis
):
    _, rider, _ = completed_pickup(world)
    requester, approver = world.supervisor(), world.supervisor()

    already_sent = pay(world, requester, rider, 700, reference())
    payout_id = pay(world, requester, rider, 700).json()["payout_id"]
    url = f"/v1/admin/payouts/{payout_id}"
    waiting = world.client.get("/v1/admin/payouts", headers=approver.headers).json()
    self_approval = world.client.post(f"{url}/approve", headers=requester.headers)
    record_early = world.client.post(
        f"{url}/record", headers=requester.headers, json={"momo_reference": reference()}
    )
    owed_while_pending = world.balance(f"rider_payable:{rider_id(world, rider)}")
    approval = world.client.post(f"{url}/approve", headers=approver.headers)
    approve_again = world.client.post(f"{url}/approve", headers=approver.headers)
    recorded = world.client.post(
        f"{url}/record", headers=requester.headers, json={"momo_reference": reference()}
    )

    assert "second supervisor" in already_sent.json()["detail"]
    assert any(p["id"] == payout_id and p["amount_pesewas"] == 700 for p in waiting)
    assert self_approval.status_code == 409
    assert record_early.json()["detail"] == "this payout is pending_approval, not approved"
    assert owed_while_pending == 700
    assert approval.status_code == 204
    assert approve_again.status_code == 409
    assert recorded.status_code == 204
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_splitting_a_payout_still_needs_a_second_supervisor(world, approval_above_5_cedis):
    _, rider, _ = completed_pickup(world)
    supervisor = world.supervisor()

    first = pay(world, supervisor, rider, 400, reference())
    second = pay(world, supervisor, rider, 300, reference())
    requested = pay(world, supervisor, rider, 300)

    assert first.json()["status"] == "recorded"
    assert "second supervisor" in second.json()["detail"]
    assert requested.json()["status"] == "pending_approval"


def test_waiting_payout_holds_the_balance_until_rejected(world, approval_above_5_cedis):
    _, rider, _ = completed_pickup(world)
    requester, reviewer = world.supervisor(), world.supervisor()

    pending = pay(world, requester, rider, 700).json()
    blocked = pay(world, requester, rider, 100, reference())
    rejected = world.client.post(
        f"/v1/admin/payouts/{pending['payout_id']}/reject",
        headers=reviewer.headers,
        json={"reason": "Wrong amount"},
    )
    after = pay(world, requester, rider, 100, reference())

    assert pending["status"] == "pending_approval"
    assert blocked.json()["detail"] == "only GHS 0.00 can be paid out now"
    assert rejected.status_code == 204
    assert after.json()["status"] == "recorded"


def test_new_payout_number_holds_payouts_and_tells_the_rider(world, no_payout_hold):
    _, rider, _ = completed_pickup(world)
    supervisor = world.supervisor()
    new_number = Actor().phone

    changed = world.client.put(
        f"/v1/admin/riders/{rider_id(world, rider)}/payout-number",
        headers=supervisor.headers,
        json={"phone": new_number},
    )
    world.tick()
    held = pay(world, supervisor, rider, 700, reference())
    world.db.run(
        "update engine.riders set payout_msisdn_changed_at = now() - interval '49 hours'"
        " where id = %s",
        rider_id(world, rider),
    )
    paid = pay(world, supervisor, rider, 700, reference())

    assert changed.status_code == 204
    assert any(new_number[-3:] in t for t in world.sms.messages_to(rider.phone))
    assert held.json()["detail"] == "the payout number changed in the last 48 hours"
    assert paid.json()["status"] == "recorded"
    assert (
        world.db.one(
            "select destination_msisdn from engine.payouts where id = %s",
            paid.json()["payout_id"],
        )["destination_msisdn"]
        == new_number
    )


def test_payout_number_cannot_change_while_a_payout_is_open(world, approval_above_5_cedis):
    _, rider, _ = completed_pickup(world)
    supervisor = world.supervisor()
    pay(world, supervisor, rider, 700)

    response = world.client.put(
        f"/v1/admin/riders/{rider_id(world, rider)}/payout-number",
        headers=supervisor.headers,
        json={"phone": Actor().phone},
    )

    assert response.json()["detail"] == "record or reject this rider's open payouts first"


def test_approved_payout_can_only_be_rejected_by_another_supervisor(world, no_payout_hold):
    _, rider, _ = completed_pickup(world)
    requester, other = world.supervisor(), world.supervisor()
    payout = pay(world, requester, rider, 700).json()
    url = f"/v1/admin/payouts/{payout['payout_id']}/reject"

    own = world.client.post(url, headers=requester.headers, json={"reason": "Changed my mind"})
    theirs = world.client.post(url, headers=other.headers, json={"reason": "Never sent"})

    assert payout["status"] == "approved"
    assert own.status_code == 409
    assert theirs.status_code == 204


# Zones -------------------------------------------------------------------------


def test_supervisor_adds_zones_and_landmarks_for_feature_phone_riders(world):
    supervisor = world.supervisor()
    name = f"Test zone {uuid4().hex[:8]}"
    try:
        zone = world.client.post(
            "/v1/admin/zones",
            headers=supervisor.headers,
            json={"name": name, "lat": 5.69, "lng": -0.17, "radius_m": 500},
        )
        zone_id = zone.json()["zone_id"]
        landmark = world.client.post(
            f"/v1/admin/zones/{zone_id}/landmarks",
            headers=supervisor.headers,
            json={"name": "Chief's palace", "lat": 5.691, "lng": -0.171},
        )
        duplicate = world.client.post(
            "/v1/admin/zones",
            headers=supervisor.headers,
            json={"name": name, "lat": 5.69, "lng": -0.17, "radius_m": 500},
        )
        listed = world.client.get("/v1/admin/zones", headers=supervisor.headers).json()
    finally:
        world.db.run(
            "delete from engine.landmarks where zone_id in"
            " (select id from engine.zones where name = %s)",
            name,
        )
        world.db.run("delete from engine.zones where name = %s", name)

    assert zone.status_code == 201
    assert landmark.status_code == 201
    assert duplicate.status_code == 409
    mine = next(z for z in listed if z["name"] == name)
    assert [lm["name"] for lm in mine["landmarks"]] == ["Chief's palace"]


def test_landmark_for_a_missing_zone_is_refused(world):
    response = world.client.post(
        "/v1/admin/zones/32000/landmarks",
        headers=world.supervisor().headers,
        json={"name": "Nowhere", "lat": 5.6, "lng": -0.1},
    )

    assert response.status_code == 404

"""Households disputing a pickup before the PIN or complaining afterwards, and
supervisors deciding, with a second approver past a daily total."""

from uuid import UUID

import pytest

from app.adapters.base import ClassifierUnavailable, Verdict, VerdictReason
from app.config import get_settings
from tests.flow import HOME, Actor, offset


def rider_id(world, rider: Actor) -> UUID:
    return world.db.one(
        "select r.id from engine.riders r join engine.users u on u.id = r.user_id"
        " where u.phone_e164 = %s",
        rider.phone,
    )["id"]


def collected(world, rider: Actor | None = None) -> tuple[Actor, Actor, UUID, str]:
    """A refuse pickup waiting for the household's PIN."""
    household, rider = world.household(), rider or world.rider(offset(HOME, 300))
    pickup_id = world.paid_pickup(household)
    pin = world.deliver_and_collect(household, rider, pickup_id)
    return household, rider, pickup_id, pin


def completed(world, rider: Actor | None = None) -> tuple[Actor, Actor, UUID]:
    household, rider, pickup_id, pin = collected(world, rider)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )
    return household, rider, pickup_id


def complain(world, household: Actor, pickup_id: UUID, details: str = "The rider never came"):
    return world.client.post(
        f"/v1/pickups/{pickup_id}/complaints", headers=household.headers, json={"details": details}
    )


def decide(world, supervisor: Actor, pickup_id: UUID, outcome: str, note: str = "Checked it"):
    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/complaint",
        headers=supervisor.headers,
        json={"outcome": outcome, "note": note},
    )
    world.tick()
    return response


def open_items(world, pickup_id: UUID) -> list[str]:
    rows = world.db.all(
        "select payload->>'reason' as reason from engine.review_items"
        " where pickup_id = %s and type = 'household_complaint' and status = 'open'"
        " order by created_at",
        pickup_id,
    )
    return [r["reason"] for r in rows]


@pytest.fixture
def daily_limits(monkeypatch):
    monkeypatch.setenv("COMPLAINT_REFUND_DAILY_PESEWAS", "1500")
    monkeypatch.setenv("COMPLAINT_POINTS_DAILY", "50")
    get_settings.cache_clear()


# Disputes before the PIN ---------------------------------------------------------------


def test_a_dispute_before_the_pin_holds_the_money_and_stops_the_pin(world):
    household, rider, pickup_id, pin = collected(world)

    response = complain(world, household, pickup_id)
    world.tick()
    household_tries = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )
    rider_tries = world.client.post(
        f"/v1/pickups/{pickup_id}/pin", headers=rider.headers, json={"pin": pin}
    )

    assert response.json() == {"status": "disputed"}
    assert world.status(pickup_id) == "disputed"
    assert household_tries.status_code == 409
    assert rider_tries.status_code == 409
    assert world.balance(f"escrow:pickup:{pickup_id}") == 1000
    assert (
        world.db.all(
            "select id from engine.pins where pickup_id = %s and status in ('active', 'locked')",
            pickup_id,
        )
        == []
    )
    assert "Payment is on hold" in world.sms.messages_to(rider.phone)[-1]
    assert open_items(world, pickup_id) == ["disputed before the PIN"]


def test_a_supervisor_can_send_a_new_pin_and_the_pickup_completes(world):
    household, rider, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)

    decided = decide(world, world.supervisor(), pickup_id, "reissue_pin")
    done = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm",
        headers=household.headers,
        json={"pin": world.pin_sent_to(household)},
    )

    assert decided.json()["status"] == "applied"
    assert done.json() == {"status": "completed"}
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 700
    assert open_items(world, pickup_id) == []


def test_a_supervisor_can_refund_a_disputed_pickup_and_the_rider_is_not_paid(world):
    household, rider, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)

    decide(world, world.supervisor(), pickup_id, "refund")
    world.refund_settles("refund-1")

    assert world.status(pickup_id) == "refunded"
    assert world.balance(f"escrow:pickup:{pickup_id}") == 0
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0
    assert "closed your pickup without payment" in world.sms.messages_to(rider.phone)[-1]
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_a_disputed_pickup_can_only_be_reissued_or_refunded(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)

    response = decide(world, world.supervisor(), pickup_id, "dismiss")

    assert response.status_code == 409
    assert world.status(pickup_id) == "disputed"


def test_an_undecided_dispute_is_raised_again_after_48_hours(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)

    world.make_due("complaint.escalate")
    world.tick()

    assert world.status(pickup_id) == "disputed"
    assert open_items(world, pickup_id) == [
        "disputed before the PIN",
        "dispute undecided for 48 hours: the money is still held",
    ]


def test_a_decided_dispute_is_not_raised_again(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)
    decide(world, world.supervisor(), pickup_id, "reissue_pin")

    world.make_due("complaint.escalate")
    world.tick()

    assert open_items(world, pickup_id) == []


# Complaints after the pickup -------------------------------------------------------------


def test_a_late_refund_is_paid_by_the_platform_and_the_rider_keeps_their_share(world):
    household, rider, pickup_id = completed(world)

    received = complain(world, household, pickup_id, "Half the bags were left behind")
    decided = decide(world, world.supervisor(), pickup_id, "refund")
    world.refund_settles("refund-1")

    assert received.json() == {"status": "received"}
    assert world.status(pickup_id) == "completed"
    assert decided.json()["status"] == "applied"
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 700
    assert world.db.one(
        "select e.amount from engine.ledger_transactions t"
        " join engine.ledger_entries e on e.txn_id = t.id"
        " join engine.ledger_accounts a on a.id = e.account_id"
        " where t.idempotency_key = %s and a.code = 'platform_adjustments'",
        f"dref:{pickup_id}",
    ) == {"amount": 1000}
    assert "GHS 10.00 is being refunded" in world.sms.messages_to(household.phone)[-1]
    assert open_items(world, pickup_id) == []
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_past_the_daily_total_a_second_supervisor_approves(world, daily_limits):
    supervisor, other = world.supervisor(), world.supervisor()
    first_home, rider, first = completed(world)
    second_home, _, second = completed(world, rider)
    complain(world, first_home, first)
    complain(world, second_home, second)

    within = decide(world, supervisor, first, "refund").json()
    held = decide(world, supervisor, second, "refund").json()
    own = world.client.post(
        f"/v1/admin/complaint-decisions/{held['decision_id']}/approve", headers=supervisor.headers
    )
    waiting = world.client.get("/v1/admin/complaint-decisions", headers=other.headers).json()
    approved = world.client.post(
        f"/v1/admin/complaint-decisions/{held['decision_id']}/approve", headers=other.headers
    )
    world.tick()

    assert within["status"] == "applied"
    assert held["status"] == "pending_approval"
    assert own.status_code == 409
    assert any(d["id"] == held["decision_id"] for d in waiting)
    assert approved.status_code == 204
    assert len(world.payments.refunds) == 2
    assert open_items(world, second) == []


def test_a_rejected_decision_leaves_the_complaint_open_for_another(world, daily_limits):
    supervisor, other = world.supervisor(), world.supervisor()
    first_home, rider, first = completed(world)
    second_home, _, second = completed(world, rider)
    complain(world, first_home, first)
    complain(world, second_home, second)
    decide(world, supervisor, first, "refund")
    held = decide(world, supervisor, second, "refund").json()

    rejected = world.client.post(
        f"/v1/admin/complaint-decisions/{held['decision_id']}/reject", headers=other.headers
    )
    dismissed = decide(world, other, second, "dismiss")

    assert rejected.status_code == 204
    assert world.db.one(
        "select status from engine.complaint_decisions where id = %s", held["decision_id"]
    ) == {"status": "rejected"}
    assert len(world.payments.refunds) == 1
    assert dismissed.json()["status"] == "applied"
    assert "found nothing to change" in world.sms.messages_to(second_home.phone)[-1]


def test_points_are_granted_back_once_for_plastic_wrongly_rejected(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.plastic_pickup(household)
    world.classifier.answer(Verdict.REJECT, 0.95, VerdictReason.CONTAMINATED)
    world.weigh_at_the_door(rider, pickup_id, weight_g=4200)
    complain(world, household, pickup_id, "It was clean PET")
    supervisor = world.supervisor()

    granted = decide(world, supervisor, pickup_id, "grant_points")
    asked_again = complain(world, household, pickup_id, "Asking again")
    again = decide(world, supervisor, pickup_id, "grant_points")

    assert granted.json()["status"] == "applied"
    assert world.client.get("/v1/points/me", headers=household.headers).json() == {
        "available": 42,
        "pending": 0,
    }
    assert asked_again.status_code == 201
    assert again.json()["detail"] == "this has already been decided for the pickup"
    assert world.balance(f"rider_payable:{rider_id(world, rider)}") == 0


def test_a_large_points_grant_waits_for_a_second_supervisor(world, daily_limits):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.plastic_pickup(household)
    world.classifier.answer(Verdict.REJECT, 0.95, VerdictReason.CONTAMINATED)
    world.weigh_at_the_door(rider, pickup_id, weight_g=6000)
    complain(world, household, pickup_id)

    held = decide(world, world.supervisor(), pickup_id, "grant_points").json()

    assert held["status"] == "pending_approval"
    assert world.client.get("/v1/points/me", headers=household.headers).json()["available"] == 0


# Rules --------------------------------------------------------------------------------


def test_complaints_close_after_seven_days(world):
    household, _, pickup_id = completed(world)
    world.db.run(
        "update engine.pickup_requests set completed_at = now() - interval '8 days' where id = %s",
        pickup_id,
    )

    assert complain(world, household, pickup_id).status_code == 409


def test_before_a_rider_is_involved_the_household_cancels_instead(world):
    household = world.household()
    pickup_id = world.paid_pickup(household)

    response = complain(world, household, pickup_id)

    assert response.status_code == 409
    assert "cancel" in response.json()["detail"]


def test_only_the_pickups_household_can_complain_and_only_once_at_a_time(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)

    stranger = complain(world, world.household(), pickup_id)
    again = complain(world, household, pickup_id)

    assert stranger.status_code == 404
    assert again.json()["detail"] == "a supervisor is already looking into this pickup"


def test_a_decision_needs_an_open_complaint(world):
    _, _, pickup_id = completed(world)

    response = decide(world, world.supervisor(), pickup_id, "refund")

    assert response.status_code == 409
    assert world.payments.refunds == []


def test_a_claimed_complaint_is_the_claimers_to_decide(world):
    household, _, pickup_id = completed(world)
    complain(world, household, pickup_id)
    first, second = world.supervisor(), world.supervisor()
    item_id = world.db.one(
        "select id from engine.review_items where pickup_id = %s and status = 'open'", pickup_id
    )["id"]
    world.client.post(f"/v1/admin/review-items/{item_id}/claim", headers=first.headers)

    theirs = decide(world, second, pickup_id, "refund")
    mine = decide(world, first, pickup_id, "dismiss")

    assert theirs.status_code == 409
    assert mine.status_code == 200


@pytest.mark.parametrize("outcome", ["grant_points", "reissue_pin"])
def test_outcomes_that_dont_fit_the_pickup_are_refused(world, outcome):
    household, _, pickup_id = completed(world)
    complain(world, household, pickup_id)

    assert decide(world, world.supervisor(), pickup_id, outcome).status_code == 409


def test_complaint_decisions_are_for_supervisors(world):
    household, _, pickup_id = completed(world)

    response = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/complaint",
        headers=household.headers,
        json={"outcome": "refund", "note": "Refund myself"},
    )

    assert response.status_code == 403


# What the review asked for ---------------------------------------------------------------


def test_a_late_refund_returns_only_what_has_not_already_gone_back(world):
    household, rider, pickup_id, pin = collected(world)
    world.extra_payment(pickup_id)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm", headers=household.headers, json={"pin": pin}
    )
    complain(world, household, pickup_id)

    decided = decide(world, world.supervisor(), pickup_id, "refund")

    assert decided.json()["status"] == "applied"
    assert len(world.payments.refunds) == 2
    assert "GHS 10.00 is being refunded" in world.sms.messages_to(household.phone)[-1]
    assert world.db.one(
        "select e.amount from engine.ledger_transactions t"
        " join engine.ledger_entries e on e.txn_id = t.id"
        " join engine.ledger_accounts a on a.id = e.account_id"
        " where t.idempotency_key = %s and a.code = 'platform_adjustments'",
        f"dref:{pickup_id}",
    ) == {"amount": 1000}


def test_a_dispute_cannot_be_closed_without_deciding_it(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)
    supervisor = world.supervisor()
    item_id = world.db.one(
        "select id from engine.review_items where pickup_id = %s and status = 'open'", pickup_id
    )["id"]

    closed = world.client.post(
        f"/v1/admin/review-items/{item_id}/resolve",
        headers=supervisor.headers,
        json={"resolution": "Sorted on the phone"},
    )
    reissued = world.client.post(
        f"/v1/admin/pickups/{pickup_id}/reissue-pin", headers=supervisor.headers
    )

    assert closed.status_code == 409
    assert reissued.status_code == 409
    assert world.status(pickup_id) == "disputed"


def test_a_dispute_is_decided_even_if_its_item_was_closed_elsewhere(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)
    world.db.run(
        "update engine.review_items set status = 'resolved', resolved_at = now(),"
        " resolution = 'closed by hand' where pickup_id = %s",
        pickup_id,
    )

    decided = decide(world, world.supervisor(), pickup_id, "refund")

    assert decided.json()["status"] == "applied"
    assert world.status(pickup_id) == "refunded"


def test_a_complaint_with_a_decision_waiting_cannot_be_dismissed(world, daily_limits):
    supervisor, other = world.supervisor(), world.supervisor()
    first_home, rider, first = completed(world)
    second_home, _, second = completed(world, rider)
    complain(world, first_home, first)
    complain(world, second_home, second)
    decide(world, supervisor, first, "refund")
    decide(world, supervisor, second, "refund")

    dismissed = decide(world, other, second, "dismiss")

    assert dismissed.json()["detail"] == "a decision is waiting for approval: approve or reject it"


def test_a_decision_cannot_be_approved_once_its_complaint_is_closed(world, daily_limits):
    supervisor, other = world.supervisor(), world.supervisor()
    first_home, rider, first = completed(world)
    second_home, _, second = completed(world, rider)
    complain(world, first_home, first)
    complain(world, second_home, second)
    decide(world, supervisor, first, "refund")
    held = decide(world, supervisor, second, "refund").json()
    world.db.run(
        "update engine.review_items set status = 'resolved', resolved_at = now(),"
        " resolution = 'closed by hand' where pickup_id = %s",
        second,
    )

    approved = world.client.post(
        f"/v1/admin/complaint-decisions/{held['decision_id']}/approve", headers=other.headers
    )

    assert approved.json()["detail"] == "the complaint was closed: reject this decision"
    assert len(world.payments.refunds) == 1


def test_a_claim_holds_even_after_the_dispute_is_raised_again(world):
    household, _, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)
    first, second = world.supervisor(), world.supervisor()
    item_id = world.db.one(
        "select id from engine.review_items where pickup_id = %s and status = 'open'", pickup_id
    )["id"]
    world.client.post(f"/v1/admin/review-items/{item_id}/claim", headers=first.headers)
    world.make_due("complaint.escalate")
    world.tick()

    theirs = decide(world, second, pickup_id, "refund")

    assert theirs.json()["detail"] == "another supervisor has claimed this complaint"


def test_a_grant_approved_by_a_second_supervisor_adds_the_points(world, daily_limits):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.plastic_pickup(household)
    world.classifier.answer(Verdict.REJECT, 0.95, VerdictReason.CONTAMINATED)
    world.weigh_at_the_door(rider, pickup_id, weight_g=6000)
    complain(world, household, pickup_id)
    held = decide(world, world.supervisor(), pickup_id, "grant_points").json()

    world.client.post(
        f"/v1/admin/complaint-decisions/{held['decision_id']}/approve",
        headers=world.supervisor().headers,
    )

    assert world.client.get("/v1/points/me", headers=household.headers).json()["available"] == 60


def test_points_voided_by_a_supervisor_can_be_granted_back(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = world.plastic_pickup(household)
    world.classifier.fail(ClassifierUnavailable("down"))
    world.weigh_at_the_door(rider, pickup_id, weight_g=4200)
    world.client.post(
        f"/v1/pickups/{pickup_id}/confirm",
        headers=household.headers,
        json={"pin": world.pin_sent_to(household)},
    )
    supervisor = world.supervisor()
    world.client.post(
        f"/v1/admin/pickups/{pickup_id}/verification",
        headers=supervisor.headers,
        json={"accept": False, "note": "Looked mixed"},
    )
    complain(world, household, pickup_id, "It was clean PET")

    granted = decide(world, supervisor, pickup_id, "grant_points")

    assert granted.json()["status"] == "applied"
    assert world.client.get("/v1/points/me", headers=household.headers).json() == {
        "available": 42,
        "pending": 0,
    }


def test_after_a_reissue_the_household_can_dispute_again(world):
    household, rider, pickup_id, _ = collected(world)
    complain(world, household, pickup_id)
    decide(world, world.supervisor(), pickup_id, "reissue_pin")

    again = complain(world, household, pickup_id, "Still not collected")
    world.tick()

    assert again.json() == {"status": "disputed"}
    assert "sent the household a new PIN" in world.sms.messages_to(rider.phone)[-2]

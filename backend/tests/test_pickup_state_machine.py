import pytest

from app.domain.pickups import (
    TERMINAL,
    TRANSITIONS,
    Event,
    InvalidTransition,
    Offering,
    Status,
    initial_status,
    next_status,
)


def reachable_from(offering: Offering, start: Status) -> set[Status]:
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for (o, source, _), target in TRANSITIONS.items():
            if o is offering and source is current and target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def reachable(offering: Offering) -> set[Status]:
    return reachable_from(offering, initial_status(offering))


def test_refuse_pickups_never_enter_verification():
    statuses = reachable(Offering.REFUSE)

    assert Status.VERIFYING not in statuses
    assert Status.RIDER_REVIEW not in statuses
    assert Status.REJECTED not in statuses


def test_plastic_pickups_never_wait_for_payment():
    assert Status.AWAITING_PAYMENT not in reachable(Offering.PLASTIC)


@pytest.mark.parametrize("offering", list(Offering))
def test_every_reachable_status_can_still_end(offering):
    for status in reachable(offering) - TERMINAL:
        ends = reachable_from(offering, status) & TERMINAL
        assert ends, f"{offering} pickup can get stuck in {status}"


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_statuses_have_no_way_out(terminal):
    assert not [key for key in TRANSITIONS if key[1] is terminal]


def test_only_a_confirmed_pin_completes_a_pickup():
    events = {event for (_, _, event), target in TRANSITIONS.items() if target is Status.COMPLETED}

    assert events == {Event.PIN_CONFIRMED}


def test_classifier_outage_never_decides_the_outcome():
    assert next_status(Offering.PLASTIC, Status.VERIFYING, Event.VERIFICATION_UNAVAILABLE) is (
        Status.AWAITING_PIN
    )


@pytest.mark.parametrize("offering", list(Offering))
@pytest.mark.parametrize("status", list(Status))
def test_household_can_cancel_only_before_the_rider_arrives(offering, status):
    allowed = {Status.AWAITING_PAYMENT, Status.PENDING_DISPATCH, Status.OFFERED, Status.ASSIGNED}
    key = (offering, status, Event.CANCELLED_BY_HOUSEHOLD)

    assert (key in TRANSITIONS) == (status in allowed and status in reachable(offering))


def test_offerings_have_their_own_collection_steps():
    with pytest.raises(InvalidTransition):
        next_status(Offering.REFUSE, Status.ARRIVED, Event.PHOTO_SUBMITTED)
    with pytest.raises(InvalidTransition):
        next_status(Offering.PLASTIC, Status.ARRIVED, Event.REFUSE_COLLECTED)


def test_a_released_offer_returns_the_pickup_for_dispatch():
    assert next_status(Offering.REFUSE, Status.OFFERED, Event.OFFER_RELEASED) is (
        Status.PENDING_DISPATCH
    )

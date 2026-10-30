"""What a rider sees of their past jobs and their money."""

import pytest

from app.config import get_settings
from tests.flow import HOME, offset
from tests.test_supervisor import complete, pay, reference


@pytest.fixture
def no_payout_hold(monkeypatch):
    monkeypatch.setenv("PAYOUT_HOLD_S", "0")
    get_settings.cache_clear()


def test_a_new_rider_has_no_history_and_nothing_owed(world):
    rider = world.rider(offset(HOME, 300))

    history = world.client.get("/v1/riders/me/history", headers=rider.headers)
    earnings = world.client.get("/v1/riders/me/earnings", headers=rider.headers).json()

    assert history.json() == []
    assert earnings["owed_pesewas"] == earnings["today_pesewas"] == earnings["jobs_today"] == 0
    assert earnings["payout_phone"] == rider.phone
    assert earnings["payouts"] == []


def test_a_completed_job_shows_in_history_with_what_it_earned(world):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    pickup_id = complete(world, household, rider)

    [job] = world.client.get("/v1/riders/me/history", headers=rider.headers).json()
    earnings = world.client.get("/v1/riders/me/earnings", headers=rider.headers).json()

    assert (job["pickup_id"], job["status"], job["earned_pesewas"]) == (
        str(pickup_id),
        "completed",
        700,
    )
    # No household details once the job is over.
    assert "address_text" not in job and "household_phone" not in job
    assert (earnings["owed_pesewas"], earnings["today_pesewas"], earnings["jobs_today"]) == (
        700,
        700,
        1,
    )


def test_a_payout_lowers_what_is_owed_but_not_what_was_earned(world, no_payout_hold):
    household, rider = world.household(), world.rider(offset(HOME, 300))
    complete(world, household, rider)
    pay(world, world.supervisor(), rider, 700, reference())

    earnings = world.client.get("/v1/riders/me/earnings", headers=rider.headers).json()

    assert (earnings["owed_pesewas"], earnings["today_pesewas"]) == (0, 700)
    assert [(p["amount_pesewas"], p["status"]) for p in earnings["payouts"]] == [(700, "recorded")]


def test_riders_see_only_their_own_jobs(world):
    household = world.household()
    worker, other = world.rider(offset(HOME, 300)), world.rider(offset(HOME, 5000))
    complete(world, household, worker)

    assert world.client.get("/v1/riders/me/history", headers=other.headers).json() == []
    assert (
        world.client.get("/v1/riders/me/earnings", headers=other.headers).json()["owed_pesewas"]
        == 0
    )


def test_households_cannot_read_rider_history(world):
    household = world.household()

    assert world.client.get("/v1/riders/me/history", headers=household.headers).status_code == 403
    assert world.client.get("/v1/riders/me/earnings", headers=household.headers).status_code == 403

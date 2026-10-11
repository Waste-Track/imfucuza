"""Spending points on vouchers that partner shops redeem by USSD, and the
monthly zone leaderboard households opt in to."""

import random
import re
import string
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.config import get_settings
from app.domain.rewards import spaced
from tests.flow import CONSENT, HOME, Actor, offset


def earn(world, household: Actor, rider: Actor, weight_g: int = 10_000) -> None:
    """A confirmed PET pickup: 10 points per kg."""
    pickup_id = world.plastic_pickup(household)
    world.weigh_at_the_door(rider, pickup_id, weight_g=weight_g)
    response = world.client.post(
        f"/v1/pickups/{pickup_id}/confirm",
        headers=household.headers,
        json={"pin": world.pin_sent_to(household)},
    )
    assert response.json() == {"status": "completed"}, response.text


def points(world, household: Actor) -> dict:
    return world.client.get("/v1/points/me", headers=household.headers).json()


def product(world, points_cost: int = 50, name: str | None = None) -> str:
    response = world.client.post(
        "/v1/admin/voucher-products",
        headers=world.supervisor().headers,
        json={
            "name": name or f"Airtime {uuid4().hex[:6]}",
            "points_cost": points_cost,
            "face_value_pesewas": 500,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["product_id"]


def buy(world, household: Actor, product_id: str, key: str | None = None):
    return world.client.post(
        "/v1/vouchers",
        headers={**household.headers, "idempotency-key": key or f"v-{uuid4()}"},
        json={"product_id": product_id},
    )


def code_for(world, household: Actor, voucher_id: str) -> str:
    mine = world.client.get("/v1/vouchers/me", headers=household.headers).json()
    return next(v["code"] for v in mine if v["id"] == voucher_id)


def bought_code(world, household: Actor, product_id: str) -> str:
    response = buy(world, household, product_id)
    assert response.status_code == 201, response.text
    return code_for(world, household, response.json()["id"])


def shop(world, name: str = "Auntie Ama Provisions") -> Actor:
    owner = Actor()
    response = world.client.post(
        "/v1/admin/shops",
        headers=world.supervisor().headers,
        json={"phone": owner.phone, "name": name, "consent_version": CONSENT},
    )
    assert response.status_code == 201, response.text
    return owner


def shop_id(world, owner: Actor) -> UUID:
    return world.db.one(
        "select s.id from engine.partner_shops s join engine.users u on u.id = s.user_id"
        " where u.phone_e164 = %s",
        owner.phone,
    )["id"]


def household_with(world, earned: int = 100) -> tuple[Actor, Actor]:
    household, rider = world.household(), world.rider(offset(HOME, 300))
    earn(world, household, rider, weight_g=earned * 100)
    return household, rider


# Vouchers ---------------------------------------------------------------------------


def test_points_buy_a_voucher_that_a_shop_redeems_once(world):
    household, _ = household_with(world, 100)
    product_id = product(world, 50, "GHS 5 airtime")
    owner = shop(world)

    bought = buy(world, household, product_id)
    world.tick()
    code = code_for(world, household, bought.json()["id"])
    texted = world.sms.messages_to(household.phone)[-1]
    checked = world.ussd(owner, "1", code)[-1]
    redeemed = world.ussd(owner, "1", code, "1")[-1]
    world.tick()
    again = world.ussd(owner, "1", code)[-1]
    elsewhere = world.ussd(shop(world, "Another shop"), "1", code)[-1]
    mine = world.client.get("/v1/vouchers/me", headers=household.headers).json()

    assert bought.status_code == 201
    assert bought.json()["code"] is None
    assert re.fullmatch(r"\d{10}", code)
    assert points(world, household) == {"available": 50, "pending": 0}
    assert spaced(code) in texted
    assert checked == "CON Valid: GHS 5 airtime.\n1 Give it now\n0 Cancel"
    assert redeemed == f"END Done. Give the customer GHS 5 airtime. Ref {code[-4:]}."
    assert again.startswith("END You redeemed this voucher on")
    assert elsewhere.startswith("END This voucher was already used on")
    assert "was used at Auntie Ama Provisions" in world.sms.messages_to(household.phone)[-1]
    assert mine[0]["status"] == "used"
    assert mine[0]["code"] is None
    assert mine[0]["used_at_shop"] == "Auntie Ama Provisions"
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_the_code_is_kept_out_of_everything_stored(world):
    household, _ = household_with(world, 100)
    response = buy(world, household, product(world), key=f"leak-{uuid4()}")
    world.tick()
    code = code_for(world, household, response.json()["id"])

    stored = world.db.one(
        "select body_masked from engine.notifications where template = 'voucher'"
        " and user_id = (select id from engine.users where phone_e164 = %s)",
        household.phone,
    )
    elsewhere = world.db.all(
        "select payload::text as text from engine.jobs where payload::text like %s"
        " union all select payload::text from engine.events where payload::text like %s"
        " union all select response_body::text from engine.idempotency_keys"
        "  where response_body::text like %s",
        f"%{code}%",
        f"%{code}%",
        f"%{code}%",
    )

    assert spaced(code) not in stored["body_masked"]
    assert "**** **** " + code[-2:] in stored["body_masked"]
    assert elsewhere == []


def test_a_voucher_costs_points_the_household_has(world):
    household, _ = household_with(world, 42)

    response = buy(world, household, product(world, 50))

    assert response.status_code == 409
    assert "costs 50 points" in response.json()["detail"]
    assert points(world, household)["available"] == 42
    assert world.client.get("/v1/vouchers/me", headers=household.headers).json() == []


def test_a_household_holds_at_most_three_unused_vouchers(world):
    household, _ = household_with(world, 100)
    product_id = product(world, 10)

    codes = [buy(world, household, product_id).status_code for _ in range(4)]

    assert codes == [201, 201, 201, 409]
    assert points(world, household)["available"] == 70


def test_buying_twice_with_the_same_key_gets_one_voucher(world):
    household, _ = household_with(world, 100)
    product_id = product(world, 30)

    first = buy(world, household, product_id, key="voucher-once")
    second = buy(world, household, product_id, key="voucher-once")

    assert first.json()["id"] == second.json()["id"]
    assert points(world, household)["available"] == 70


def test_a_withdrawn_product_cannot_be_bought_but_issued_vouchers_still_work(world):
    household, _ = household_with(world, 100)
    product_id = product(world, 30)
    code = bought_code(world, household, product_id)
    world.client.put(
        f"/v1/admin/voucher-products/{product_id}/active",
        headers=world.supervisor().headers,
        json={"active": False},
    )

    refused = buy(world, household, product_id)
    catalogue = world.client.get("/v1/vouchers/catalog", headers=household.headers).json()

    assert refused.status_code == 409
    assert product_id not in [p["id"] for p in catalogue]
    assert world.ussd(shop(world), "1", code)[-1].startswith("CON Valid")


def test_an_unused_voucher_expires_and_gives_its_points_back(world):
    household, _ = household_with(world, 100)
    voucher_id = buy(world, household, product(world, 60)).json()["id"]
    code = code_for(world, household, voucher_id)
    world.db.run(
        "update engine.vouchers set expires_at = now() - interval '1 minute' where id = %s",
        voucher_id,
    )
    stale = world.ussd(shop(world), "1", code)[-1]

    world.make_due("voucher.expire")
    world.tick()

    assert stale.startswith("END This voucher expired on")
    assert points(world, household)["available"] == 100
    assert "60 points are back" in world.sms.messages_to(household.phone)[-1]
    assert world.db.all("select * from engine.ledger_reconcile()") == []


def test_a_used_voucher_never_gives_its_points_back(world):
    household, _ = household_with(world, 100)
    code = bought_code(world, household, product(world, 60))
    world.ussd(shop(world), "1", code, "1")
    world.db.run("update engine.vouchers set expires_at = now() where code = %s", code)

    world.make_due("voucher.expire")
    world.tick()

    assert points(world, household)["available"] == 40


def test_when_two_shops_try_one_voucher_only_one_gives_it(world):
    household, _ = household_with(world, 100)
    code = bought_code(world, household, product(world, 30))
    first, second = shop(world, "First shop"), shop(world, "Second shop")

    looked = world.ussd(first, "1", code)[-1]
    world.ussd(second, "1", code, "1")
    late = world.ussd(first, "1", code, "1")[-1]

    assert looked.startswith("CON Valid")
    assert late.startswith("END This voucher was already used on")


# Shops ---------------------------------------------------------------------------------


def test_a_shop_typing_wrong_codes_is_stopped_but_other_shops_are_not(world):
    household, _ = household_with(world, 100)
    code = bought_code(world, household, product(world, 30))
    guesser, honest = shop(world, "Guesser"), shop(world, "Honest")
    wrong = [f"{random.randrange(10**10):010d}" for _ in range(10)]

    for guess in wrong:
        world.ussd(guesser, "1", guess)
    stopped = world.ussd(guesser, "1", code)[-1]

    assert stopped == "END Too many wrong codes. Please try again in an hour."
    assert world.ussd(honest, "1", code)[-1].startswith("CON Valid")


def test_a_short_code_is_asked_for_again_and_not_counted(world):
    owner = shop(world)

    shown = world.ussd(owner, "1", "12345")[-1]

    assert shown == "CON A voucher code has 10 digits. Type it again:"
    assert (
        world.db.one(
            "select count(*) as n from engine.events where name = 'voucher.wrong_code'"
            " and actor_id = (select id from engine.users where phone_e164 = %s)",
            owner.phone,
        )["n"]
        == 0
    )


def test_a_suspended_shop_cannot_redeem(world):
    owner = shop(world)

    world.client.put(
        f"/v1/admin/shops/{shop_id(world, owner)}/active",
        headers=world.supervisor().headers,
        json={"active": False},
    )

    assert world.ussd(owner)[0].startswith("END This number is not registered")


def test_menus_follow_the_phone(world):
    owner = shop(world)
    household = world.household()

    assert world.ussd(owner)[0] == "CON Imfucuza shop\n1 Redeem a voucher"
    assert "Redeem a voucher" not in world.ussd(household)[0]


def test_supervisors_see_what_each_shop_redeemed_this_month_only(world):
    household, _ = household_with(world, 100)
    owner = shop(world, f"Shop {uuid4().hex[:6]}")
    product_id = product(world, 30)
    for _ in range(2):
        world.ussd(owner, "1", bought_code(world, household, product_id), "1")
    world.db.run(
        "update engine.vouchers set used_at = used_at - interval '40 days'"
        " where id = (select id from engine.vouchers where used_at_shop_id = %s limit 1)",
        shop_id(world, owner),
    )

    report = world.client.get(
        "/v1/admin/shops/redeemed",
        headers=world.supervisor().headers,
        params={
            "month": datetime.now(UTC).strftime("%Y-%m"),
            "shop_id": str(shop_id(world, owner)),
        },
    ).json()

    assert [(r["vouchers"], r["face_value_pesewas"]) for r in report] == [(1, 500)]


def test_a_supervisor_sees_a_households_vouchers_without_codes(world):
    household, _ = household_with(world, 100)
    bought_code(world, household, product(world, 30))

    seen = world.client.get(
        "/v1/admin/vouchers", headers=world.supervisor().headers, params={"phone": household.phone}
    ).json()

    assert [(v["status"], v["code"]) for v in seen] == [("active", None)]


def test_rewards_administration_is_for_supervisors(world):
    household = world.household()

    assert (
        world.client.get("/v1/admin/voucher-products", headers=household.headers).status_code == 403
    )
    assert (
        world.client.get("/v1/vouchers/me", headers=world.supervisor().headers).status_code == 403
    )


# The leaderboard ------------------------------------------------------------------


def nick(base: str) -> str:
    """Names are unique across households, and tests share a database."""
    return f"{base} {''.join(random.choices(string.ascii_lowercase, k=6))}"


@pytest.fixture
def zone(world):
    """A zone of our own, far from the seeded ones, so other tests' households
    don't share the board."""
    name = f"Test zone {uuid4().hex[:8]}"
    centre = (6.0 + random.random(), -1.0 - random.random())
    row = world.db.one(
        "insert into engine.zones (name, lat, lng, radius_m, ussd_index)"
        " select %s, %s, %s, 800, coalesce(max(ussd_index), 0) + 1 from engine.zones"
        " returning id",
        name,
        *centre,
    )
    try:
        yield row["id"], centre
    finally:
        # Households left here would otherwise join whichever zone is nearest next.
        world.db.run(
            "update engine.households h set lat = null, lng = null"
            " where engine.household_zone(h) = %s",
            row["id"],
        )
        world.db.run("delete from engine.zones where id = %s", row["id"])


def join(world, household: Actor, name: str):
    return world.client.put(
        "/v1/households/me/leaderboard",
        headers=household.headers,
        json={"name": name, "consent_version": get_settings().leaderboard_consent_version},
    )


def neighbour(world, centre, rider: Actor, earned: int, name: str | None = None) -> Actor:
    household = world.household(centre)
    earn(world, household, rider, weight_g=earned * 100)
    if name:
        assert join(world, household, name).status_code == 204
    return household


def board(world, household: Actor, **params) -> dict:
    response = world.client.get("/v1/leaderboard", headers=household.headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_only_households_that_opt_in_are_ranked_and_others_see_where_they_would_be(world, zone):
    zone_id, centre = zone
    rider = world.rider(offset(centre, 300))
    ama, kojo = nick("Ama"), nick("Kojo")
    leader = neighbour(world, centre, rider, 100, ama)
    quiet = neighbour(world, centre, rider, 60)
    third = neighbour(world, centre, rider, 30, kojo)

    seen_by_quiet = board(world, quiet)

    assert seen_by_quiet["zone"]["id"] == zone_id
    assert seen_by_quiet["top"] == [
        {"rank": 1, "name": ama, "points": 100},
        {"rank": 2, "name": kojo, "points": 30},
    ]
    assert seen_by_quiet["you"] == {"rank": 2, "points": 60, "on_board": False}
    assert board(world, leader)["you"] == {"rank": 1, "points": 100, "on_board": True}
    assert board(world, third)["you"]["rank"] == 2


def test_spending_points_does_not_lower_a_rank(world, zone):
    _, centre = zone
    rider = world.rider(offset(centre, 300))
    household = neighbour(world, centre, rider, 100, nick("Esi"))

    buy(world, household, product(world, 80))

    assert board(world, household)["you"]["points"] == 100


@pytest.mark.parametrize(
    "name",
    ["Ama 0244123456", "Call0244", "Imfucuza", "supervisor", "A", "x" * 25],
)
def test_names_that_could_mislead_or_carry_contact_details_are_refused(world, zone, name):
    _, centre = zone
    household = world.household(centre)

    assert join(world, household, name).status_code == 422


def test_a_name_belongs_to_one_household(world, zone):
    _, centre = zone
    name = nick("Akosua")
    first, second = world.household(centre), world.household(centre)
    join(world, first, name)

    taken = join(world, second, name.upper())

    assert taken.status_code == 409


def test_joining_needs_the_notice_and_the_event_log_keeps_the_consent(world, zone):
    _, centre = zone
    rider = world.rider(offset(centre, 300))
    household = neighbour(world, centre, rider, 50)
    name = nick("Yaa")

    without_notice = world.client.put(
        "/v1/households/me/leaderboard", headers=household.headers, json={"name": name}
    )
    join(world, household, f"  {name}  ")
    named = board(world, household)["top"]
    world.client.put("/v1/households/me/leaderboard", headers=household.headers, json={})
    stored = world.db.one(
        "select leaderboard_name, leaderboard_consent_at from engine.households"
        " where user_id = (select id from engine.users where phone_e164 = %s)",
        household.phone,
    )
    logged = world.db.all(
        "select e.payload from engine.events e join engine.users u on u.id = e.actor_id"
        " where e.name = 'household.leaderboard' and u.phone_e164 = %s order by e.id",
        household.phone,
    )

    assert without_notice.status_code == 422
    assert named == [{"rank": 1, "name": name, "points": 50}]
    assert board(world, household)["top"] == []
    assert stored == {"leaderboard_name": None, "leaderboard_consent_at": None}
    assert [(e["payload"]["joined"], e["payload"]["consent_version"]) for e in logged] == [
        (True, get_settings().leaderboard_consent_version),
        (False, None),
    ]


def test_a_supervisor_can_take_a_name_off_the_board(world, zone):
    _, centre = zone
    rider = world.rider(offset(centre, 300))
    household = neighbour(world, centre, rider, 50, nick("Rude"))
    household_id = world.db.one(
        "select h.id from engine.households h join engine.users u on u.id = h.user_id"
        " where u.phone_e164 = %s",
        household.phone,
    )["id"]

    cleared = world.client.delete(
        f"/v1/admin/households/{household_id}/leaderboard-name",
        headers=world.supervisor().headers,
    )

    assert cleared.status_code == 204
    assert board(world, household)["top"] == []


def test_last_months_board_is_separate(world, zone):
    _, centre = zone
    rider = world.rider(offset(centre, 300))
    household = neighbour(world, centre, rider, 50, nick("Akua"))

    last_month = board(world, household, month="2026-01")
    nonsense = world.client.get(
        "/v1/leaderboard", headers=household.headers, params={"month": "0000-01"}
    )

    assert last_month["month"] == "2026-01"
    assert last_month["top"] == []
    assert last_month["you"] is None
    assert nonsense.status_code == 422


def test_households_in_another_zone_are_not_on_the_board(world, zone):
    _, centre = zone
    rider = world.rider(offset(centre, 300))
    name = nick("Abena")
    mine = neighbour(world, centre, rider, 50, name)
    elsewhere, _ = household_with(world, 90)

    assert board(world, mine)["top"] == [{"rank": 1, "name": name, "points": 50}]
    assert board(world, elsewhere)["zone"]["id"] != board(world, mine)["zone"]["id"]

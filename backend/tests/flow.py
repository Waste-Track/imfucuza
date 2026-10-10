"""Drive the Engine over HTTP as households, riders and providers would.

Jobs run through POST /internal/tick, the same way Supabase Cron runs them.
"""

import re
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import psycopg
from fastapi import Request
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.adapters.base import PaymentEventKind, RefundState
from app.adapters.fakes import FakePaymentProvider, FakeSmsGateway
from app.auth import Principal
from app.config import get_settings
from tests.helpers import LOCAL_HOSTS

TICK_SECRET = "test-tick-secret"
CONSENT = "2026-10-v1"
# Madina, Accra.
HOME = (5.6837, -0.1657)


def principal_from_headers(request: Request) -> Principal:
    """Stands in for Supabase token verification in flow tests."""
    phone = request.headers.get("x-test-phone")
    return Principal(UUID(request.headers["x-test-sub"]), phone.lstrip("+") if phone else None)


def random_phone() -> str:
    return "+2335" + str(uuid4().int)[:8]


def offset(point: tuple[float, float], metres_north: float) -> tuple[float, float]:
    return point[0] + metres_north / 111_320, point[1]


@dataclass
class Actor:
    phone: str = field(default_factory=random_phone)
    sub: UUID = field(default_factory=uuid4)

    @property
    def headers(self) -> dict[str, str]:
        return {"x-test-sub": str(self.sub), "x-test-phone": self.phone}


class Db:
    def __init__(self) -> None:
        url = get_settings().database_url
        # The world fixture empties the job queue: never point it at a real database.
        if conninfo_to_dict(url).get("host") not in LOCAL_HOSTS:
            raise RuntimeError("flow tests only run against a local database")
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def one(self, query: str, *params):
        return self.conn.execute(query, params).fetchone()

    def all(self, query: str, *params):
        return self.conn.execute(query, params).fetchall()

    def run(self, query: str, *params) -> None:
        self.conn.execute(query, params)


@dataclass
class World:
    client: TestClient
    payments: FakePaymentProvider
    sms: FakeSmsGateway
    db: Db

    def tick(self, rounds: int = 10) -> int:
        """Run due jobs until none are left. Returns how many ran."""
        total = 0
        for _ in range(rounds):
            response = self.client.post(
                "/internal/tick", headers={"x-internal-secret": TICK_SECRET}
            )
            assert response.status_code == 200, response.text
            ran = sum(response.json().values())
            total += ran
            if ran == 0:
                break
        return total

    def make_due(self, kind: str) -> None:
        self.db.run(
            "update engine.jobs set run_at = now() where kind = %s and done_at is null", kind
        )

    def household(self, location: tuple[float, float] = HOME) -> Actor:
        actor = Actor()
        response = self.client.put(
            "/v1/households/me",
            headers=actor.headers,
            json={"consent_version": CONSENT, "lat": location[0], "lng": location[1]},
        )
        assert response.status_code == 200, response.text
        return actor

    def supervisor(self) -> Actor:
        actor = Actor()
        self.db.run(
            """
            insert into engine.users
                (auth_user_id, role, phone_e164, consent_version, consent_at, consent_channel)
            values (%s, 'supervisor', %s, %s, now(), 'paper')
            """,
            actor.sub,
            actor.phone,
            CONSENT,
        )
        return actor

    def rider(self, location: tuple[float, float], channel: str = "pwa") -> Actor:
        rider = Actor()
        response = self.client.post(
            "/v1/admin/riders",
            headers=self.supervisor().headers,
            json={
                "phone": rider.phone,
                "name": "Kofi",
                "channel": channel,
                "consent_version": CONSENT,
            },
        )
        assert response.status_code == 201, response.text
        assert (
            self.client.put(
                "/v1/riders/me/duty", headers=rider.headers, json={"on_duty": True}
            ).status_code
            == 200
        )
        self.move(rider, location)
        return rider

    def move(self, rider: Actor, location: tuple[float, float]) -> None:
        response = self.client.post(
            "/v1/riders/me/locations",
            headers=rider.headers,
            json={
                "fixes": [
                    {
                        "lat": location[0],
                        "lng": location[1],
                        "accuracy_m": 10,
                        "recorded_at": _now_iso(),
                    }
                ]
            },
        )
        assert response.status_code == 204, response.text

    def request_pickup(self, household: Actor, key: str | None = None) -> UUID:
        response = self.client.post(
            "/v1/pickups",
            headers={**household.headers, "idempotency-key": key or f"req-{uuid4()}"},
            json={"offering": "refuse"},
        )
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"])

    def pay(self, household: Actor, pickup_id: UUID, *, webhook: bool = True) -> str:
        response = self.client.post(
            f"/v1/pickups/{pickup_id}/payment", headers=household.headers, json={"network": "mtn"}
        )
        assert response.status_code == 200, response.text
        reference = response.json()["reference"]
        self.payments.settle(reference)
        if webhook:
            self.webhook(PaymentEventKind.CHARGE_SUCCEEDED, reference)
        return reference

    def webhook(self, kind: PaymentEventKind, reference: str):
        body, headers = FakePaymentProvider.webhook(kind, reference)
        return self.client.post("/webhooks/payments/fake", content=body, headers=headers)

    def refund_settles(self, refund_id: str, state: RefundState = RefundState.SUCCEEDED) -> None:
        body, headers = self.payments.refund_outcome(refund_id, state)
        response = self.client.post("/webhooks/payments/fake", content=body, headers=headers)
        assert response.status_code == 200, response.text
        self.tick()

    def paid_pickup(self, household: Actor) -> UUID:
        pickup_id = self.request_pickup(household)
        self.pay(household, pickup_id)
        self.tick()
        return pickup_id

    def status(self, pickup_id: UUID) -> str:
        return self.db.one("select status from engine.pickup_requests where id = %s", pickup_id)[
            "status"
        ]

    def open_offer(self, rider: Actor) -> dict:
        offers = self.client.get("/v1/riders/me/offers", headers=rider.headers).json()
        assert len(offers) == 1, offers
        return offers[0]

    def balance(self, code: str) -> int:
        row = self.db.one("select balance from engine.ledger_accounts where code = %s", code)
        return row["balance"] if row else 0

    def pin_sent_to(self, household: Actor) -> str:
        texts = self.sms.messages_to(household.phone)
        pins = [m.group(1) for t in texts if (m := re.search(r"PIN is (\d{6})", t))]
        assert pins, texts
        return pins[-1]

    def deliver_and_collect(self, household: Actor, rider: Actor, pickup_id: UUID) -> str:
        """Accept the open offer, arrive, collect, and return the PIN texted to
        the household."""
        offer = self.open_offer(rider)
        assert (
            self.client.post(f"/v1/offers/{offer['id']}/accept", headers=rider.headers).status_code
            == 200
        )
        return self.arrive_and_collect(household, rider, pickup_id)

    def arrive_and_collect(self, household: Actor, rider: Actor, pickup_id: UUID) -> str:
        near = offset(HOME, 40)
        response = self.client.post(
            f"/v1/pickups/{pickup_id}/arrive",
            headers=rider.headers,
            json={"lat": near[0], "lng": near[1], "accuracy_m": 10, "recorded_at": _now_iso()},
        )
        assert response.status_code == 200, response.text
        assert (
            self.client.post(
                f"/v1/pickups/{pickup_id}/collected", headers=rider.headers
            ).status_code
            == 200
        )
        self.tick()
        return self.pin_sent_to(household)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()

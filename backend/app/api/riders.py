from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from psycopg import errors
from pydantic import BaseModel, Field, field_validator

from app.api import uploads
from app.api.common import PinEntry, pin_response
from app.api.deps import PrincipalDep, RiderBriefDep, RiderDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import dispatch, people, pickup_flow, pins, plastic

router = APIRouter(prefix="/v1", tags=["riders"])


class RiderApplication(BaseModel):
    consent_version: str = Field(description="The consent notice version the rider accepted")
    name: str = Field(min_length=2, max_length=100)
    # Where earnings are sent. Defaults to the number signed in with.
    payout_phone: str | None = None
    tricycle_plate: str = Field(min_length=2, max_length=20)
    work_area: str = Field(min_length=2, max_length=100)


@router.put("/riders/me", status_code=201)
async def apply(body: RiderApplication, conn: ConnDep, principal: PrincipalDep) -> dict:
    """A smartphone rider applies. A supervisor approves the application
    (PUT /v1/admin/riders/{id}/status) before any offers come."""
    if body.consent_version != get_settings().consent_version:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"accept consent notice {get_settings().consent_version} first",
        )
    if not principal.phone:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="sign in with a phone number")
    try:
        phone = people.require_ghana(people.normalize_msisdn(principal.phone))
        payout = (
            people.require_ghana(people.normalize_msisdn(body.payout_phone))
            if body.payout_phone
            else phone
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    try:
        rider_id = await people.apply_as_rider(
            conn,
            auth_user_id=principal.auth_user_id,
            phone_e164=phone,
            name=body.name.strip(),
            payout_msisdn=payout,
            tricycle_plate=body.tricycle_plate.strip().upper(),
            work_area=body.work_area.strip(),
            consent_version=body.consent_version,
        )
    except errors.UniqueViolation as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="this phone number is already registered"
        ) from exc
    return {"rider_id": rider_id, "status": "pending"}


class Duty(BaseModel):
    on_duty: bool


@router.put("/riders/me/duty")
async def set_duty(body: Duty, conn: ConnDep, rider: RiderDep) -> Duty:
    await dispatch.set_duty(
        conn, rider.rider_id, on_duty=body.on_duty, actor_type="rider", user_id=rider.user.id
    )
    return body


class Fix(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: int | None = Field(default=None, ge=0)
    recorded_at: datetime
    mock_location: bool = False

    @field_validator("recorded_at")
    @classmethod
    def recent(cls, value: datetime) -> datetime:
        now = datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("recorded_at needs a timezone")
        if value > now + timedelta(minutes=2) or value < now - timedelta(days=1):
            raise ValueError("recorded_at must be within the last day and not in the future")
        return value

    def to_fix(self) -> dispatch.LocationFix:
        return dispatch.LocationFix(
            self.lat, self.lng, self.accuracy_m, self.recorded_at, self.mock_location
        )


class Fixes(BaseModel):
    fixes: list[Fix] = Field(min_length=1, max_length=100)


@router.post("/riders/me/locations", status_code=204)
async def report_locations(body: Fixes, conn: ConnDep, rider: RiderDep) -> None:
    await dispatch.record_locations(conn, rider.rider_id, [f.to_fix() for f in body.fixes])


class OfferView(BaseModel):
    id: UUID
    pickup_id: UUID
    offering: str
    distance_m: int
    expires_at: datetime
    # Refuse pays a share of the fee. Plastic pays per verified kg.
    earning_pesewas: int | None
    pay_per_kg_pesewas: int | None


@router.get("/riders/me/offers")
async def my_offers(conn: ConnDep, rider: RiderDep) -> list[OfferView]:
    cur = await conn.execute(
        """
        select o.id, o.pickup_id, p.offering, o.distance_m, o.expires_at,
               p.fee_pesewas * %s / 100 as earning_pesewas,
               case when p.offering = 'plastic' then %s end as pay_per_kg_pesewas
          from engine.dispatch_offers o
          join engine.pickup_requests p on p.id = o.pickup_id
         where o.rider_id = %s and o.response is null and o.expires_at > now()
        """,
        (_rider_share(), get_settings().rider_pay_pesewas_per_kg, rider.rider_id),
    )
    return [OfferView(**row) for row in await cur.fetchall()]


@router.post("/offers/{offer_id}/accept")
async def accept(offer_id: UUID, conn: ConnDep, rider: RiderDep) -> dict:
    result = await dispatch.accept(conn, offer_id, rider_id=rider.rider_id, user_id=rider.user.id)
    return {"pickup_id": result.pickup_id, "status": result.status}


@router.post("/offers/{offer_id}/decline", status_code=204)
async def decline(offer_id: UUID, conn: ConnDep, rider: RiderDep) -> None:
    await dispatch.decline(conn, offer_id, rider_id=rider.rider_id, user_id=rider.user.id)


class JobView(BaseModel):
    pickup_id: UUID
    offering: str
    status: str
    lat: float
    lng: float
    address_text: str | None
    # Only while the rider is on the way or at the door (threat model, item 19).
    household_phone: str | None
    # For plastic, known once the rider has weighed it.
    earning_pesewas: int | None
    declared_type: str | None
    weight_g: int | None
    verification_outcome: str


@router.get("/riders/me/jobs")
async def my_jobs(conn: ConnDep, rider: RiderDep) -> list[JobView]:
    cur = await conn.execute(
        """
        select p.id as pickup_id, p.offering, p.status, p.lat, p.lng, h.address_text,
               case when p.status = any(%s) then u.phone_e164 end as household_phone,
               coalesce(p.fee_pesewas * %s / 100, p.rider_pay_pesewas) as earning_pesewas,
               p.declared_type, p.weight_g, p.verification_outcome
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
          join engine.users u on u.id = h.user_id
         where p.assigned_rider_id = %s and p.status = any(%s)
         order by p.assigned_at
        """,
        (
            list(dispatch.ACTIVE_JOB_STATUSES),
            _rider_share(),
            rider.rider_id,
            list(pickup_flow.ACTIVE_FOR_RIDER),
        ),
    )
    return [JobView(**row) for row in await cur.fetchall()]


class PastJob(BaseModel):
    pickup_id: UUID
    offering: str
    status: str
    finished_at: datetime
    # What the ledger credited the rider for it: 0 unless it was completed.
    earned_pesewas: int


@router.get("/riders/me/history")
async def my_history(
    conn: ConnDep, rider: RiderDep, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[PastJob]:
    # No address or phone: the rider only needed those while the job was on.
    cur = await conn.execute(
        """
        select p.id as pickup_id, p.offering, p.status,
               coalesce(p.completed_at, p.updated_at) as finished_at,
               (select coalesce(sum(e.amount), 0)
                  from engine.ledger_entries e
                  join engine.ledger_transactions t on t.id = e.txn_id
                  join engine.ledger_accounts a on a.id = e.account_id
                 where t.pickup_id = p.id and a.code = %(account)s and e.side = 'credit')
                 as earned_pesewas
          from engine.pickup_requests p
         where p.assigned_rider_id = %(rider)s and not (p.status = any(%(active)s))
         order by finished_at desc
         limit %(limit)s
        """,
        {
            "account": _payable_account(rider.rider_id),
            "rider": rider.rider_id,
            "active": list(pickup_flow.ACTIVE_FOR_RIDER),
            "limit": limit,
        },
    )
    return [PastJob(**row) for row in await cur.fetchall()]


class PayoutView(BaseModel):
    id: UUID
    amount_pesewas: int
    status: str
    created_at: datetime
    recorded_at: datetime | None


class Earnings(BaseModel):
    # Earned and not yet paid out.
    owed_pesewas: int
    today_pesewas: int
    week_pesewas: int
    jobs_today: int
    payout_phone: str | None
    payouts: list[PayoutView]


@router.get("/riders/me/earnings")
async def my_earnings(conn: ConnDep, rider: RiderDep) -> Earnings:
    cur = await conn.execute(
        """
        select coalesce((select balance from engine.ledger_accounts where code = %(account)s), 0)
                 as owed_pesewas,
               coalesce(sum(e.amount) filter (where e.created_at >= date_trunc('day', now())), 0)
                 as today_pesewas,
               coalesce(sum(e.amount) filter (where e.created_at >= date_trunc('week', now())), 0)
                 as week_pesewas,
               (select count(*) from engine.pickup_requests p
                 where p.assigned_rider_id = %(rider)s and p.status = 'completed'
                   and p.completed_at >= date_trunc('day', now())) as jobs_today,
               (select payout_msisdn from engine.riders where id = %(rider)s) as payout_phone
          from engine.ledger_entries e
          join engine.ledger_accounts a on a.id = e.account_id
         where a.code = %(account)s and e.side = 'credit'
        """,
        {"account": _payable_account(rider.rider_id), "rider": rider.rider_id},
    )
    totals = await cur.fetchone()
    cur = await conn.execute(
        """
        select id, amount_pesewas, status, created_at, recorded_at
          from engine.payouts
         where rider_id = %s
         order by created_at desc
         limit 20
        """,
        (rider.rider_id,),
    )
    payouts = [PayoutView(**row) for row in await cur.fetchall()]
    return Earnings(**totals, payouts=payouts)


def _payable_account(rider_id: UUID) -> str:
    return f"rider_payable:{rider_id}"


@router.post("/pickups/{pickup_id}/arrive")
async def arrive(pickup_id: UUID, body: Fix, conn: ConnDep, rider: RiderDep) -> dict:
    result = await pickup_flow.arrive(
        conn, pickup_id, rider_id=rider.rider_id, user_id=rider.user.id, fix=body.to_fix()
    )
    return {"status": result.status}


@router.post("/pickups/{pickup_id}/collected")
async def collected(pickup_id: UUID, conn: ConnDep, rider: RiderDep) -> dict:
    result = await pickup_flow.collected(
        conn, pickup_id, rider_id=rider.rider_id, user_id=rider.user.id
    )
    return {"status": result.status}


@router.post("/pickups/{pickup_id}/plastic")
async def submit_plastic(
    pickup_id: UUID,
    request: Request,
    rider: RiderBriefDep,
    weight_g: Annotated[int, Query(gt=0)],
) -> dict:
    """The photo is the request body (JPEG, PNG or WebP), the weight a query
    parameter. Taken at the door, once the rider has weighed the plastic."""
    pool = request.app.state.pool
    async with pool.connection() as conn, conn.transaction():
        await plastic.expect_rider_photo(conn, pickup_id, rider.rider_id, weight_g)
    photo = await uploads.store(request, pickup_id)
    async with uploads.kept_if_recorded(photo), pool.connection() as conn, conn.transaction():
        result = await plastic.submit_from_rider(
            conn,
            pickup_id,
            rider_id=rider.rider_id,
            user_id=rider.user.id,
            photo_id=photo.id,
            storage_key=photo.storage_key,
            photo=photo.prepared,
            weight_g=weight_g,
        )
    return {"status": result.status}


class RiderDecision(BaseModel):
    accept: bool


@router.post("/pickups/{pickup_id}/rider-decision")
async def rider_decision(
    pickup_id: UUID, body: RiderDecision, conn: ConnDep, rider: RiderDep
) -> dict:
    """For plastic the photo check was unsure about: the rider looks and decides."""
    new_status = await plastic.rider_decides(
        conn, pickup_id, rider_id=rider.rider_id, user_id=rider.user.id, accept=body.accept
    )
    return {"status": new_status}


@router.post("/pickups/{pickup_id}/pin")
async def enter_household_pin(
    pickup_id: UUID, body: PinEntry, conn: ConnDep, rider: RiderDep
) -> JSONResponse:
    """The household reads its PIN to the rider. Logged as weaker evidence than
    the household confirming on its own phone."""
    await pickup_flow.assigned_to(conn, pickup_id, rider.rider_id)
    result = await pins.confirm(
        conn,
        pickup_id,
        body.pin,
        via="rider_entry",
        actor_type="rider",
        actor_user_id=rider.user.id,
    )
    return pin_response(result)


def _rider_share() -> int:
    return get_settings().rider_share_percent

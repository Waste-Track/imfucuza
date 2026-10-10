from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.api.common import PinEntry, pin_response
from app.api.deps import RiderDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import dispatch, pickup_flow, pins

router = APIRouter(prefix="/v1", tags=["riders"])


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
    distance_m: int
    expires_at: datetime
    earning_pesewas: int


@router.get("/riders/me/offers")
async def my_offers(conn: ConnDep, rider: RiderDep) -> list[OfferView]:
    cur = await conn.execute(
        """
        select o.id, o.pickup_id, o.distance_m, o.expires_at,
               p.fee_pesewas * %s / 100 as earning_pesewas
          from engine.dispatch_offers o
          join engine.pickup_requests p on p.id = o.pickup_id
         where o.rider_id = %s and o.response is null and o.expires_at > now()
        """,
        (_rider_share(), rider.rider_id),
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
    status: str
    lat: float
    lng: float
    address_text: str | None
    # Only while the rider is on the way or at the door (threat model, item 19).
    household_phone: str | None
    earning_pesewas: int


@router.get("/riders/me/jobs")
async def my_jobs(conn: ConnDep, rider: RiderDep) -> list[JobView]:
    cur = await conn.execute(
        """
        select p.id as pickup_id, p.status, p.lat, p.lng, h.address_text,
               case when p.status = any(%s) then u.phone_e164 end as household_phone,
               p.fee_pesewas * %s / 100 as earning_pesewas
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

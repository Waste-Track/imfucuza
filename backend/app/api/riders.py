import contextlib
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app import services
from app.adapters.base import ProviderError
from app.api.common import PinEntry, pin_response
from app.api.deps import RiderBriefDep, RiderDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import dispatch, photos, pickup_flow, pins, plastic

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
    parameter. Taken at the door, once the rider has weighed the plastic.

    No database connection is held while the photo uploads and is processed:
    slow uploads would otherwise starve every other request."""
    pool = request.app.state.pool
    async with pool.connection() as conn, conn.transaction():
        await plastic.expect_material(conn, pickup_id, rider.rider_id, weight_g)
    data = await _read_upload(request, photos.MAX_UPLOAD_BYTES)
    photo = await anyio.to_thread.run_sync(
        photos.prepare, data, limiter=request.app.state.photo_limiter
    )
    photo_id = uuid4()
    storage_key = f"{pickup_id}/{photo_id}.jpg"
    store = services.get().photos
    try:
        await store.put(storage_key, photo.jpeg, "image/jpeg")
    except ProviderError as exc:
        if exc.retryable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="the photo couldn't be stored, try again",
            ) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="the photo couldn't be stored"
        ) from exc
    try:
        async with pool.connection() as conn, conn.transaction():
            result = await plastic.submit(
                conn,
                pickup_id,
                rider_id=rider.rider_id,
                user_id=rider.user.id,
                photo_id=photo_id,
                storage_key=storage_key,
                photo=photo,
                weight_g=weight_g,
            )
    except Exception:
        # A photo with no record would never be found or deleted again.
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(ProviderError):
                await store.delete(storage_key)
        raise
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


async def _read_upload(request: Request, limit: int) -> bytes:
    """Read the body, giving up as soon as it passes `limit`."""
    too_big = HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE, detail=f"the photo is larger than {limit // 2**20} MB"
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


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

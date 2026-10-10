from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from psycopg import errors
from pydantic import BaseModel, Field, field_validator

from app.api.deps import SupervisorDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import people, supervision

router = APIRouter(prefix="/v1/admin", tags=["supervisor"])


class NewRider(BaseModel):
    phone: str
    name: str = Field(min_length=1, max_length=100)
    channel: Literal["pwa", "ussd"]
    consent_version: str = Field(description="Version of the consent form the rider signed")


@router.post("/riders", status_code=201)
async def register_rider(body: NewRider, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    if body.consent_version != get_settings().consent_version:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"use consent form {get_settings().consent_version}",
        )
    try:
        phone = people.require_ghana(people.normalize_msisdn(body.phone))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    try:
        rider_id = await people.register_rider(
            conn,
            phone_e164=phone,
            name=body.name,
            channel=body.channel,
            consent_version=body.consent_version,
            registered_by=supervisor.id,
        )
    except errors.UniqueViolation as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="this phone number is already registered"
        ) from exc
    return {"rider_id": rider_id}


# Review queue ----------------------------------------------------------------


@router.get("/review-items")
async def review_items(
    conn: ConnDep,
    supervisor: SupervisorDep,
    status_: Literal["open", "resolved"] = Query("open", alias="status"),
    type_: str | None = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    return await supervision.list_review_items(conn, status=status_, type_=type_, limit=limit)


@router.get("/review-items/{item_id}")
async def review_item(item_id: UUID, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    detail = await supervision.review_item_detail(conn, item_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="review item not found")
    return detail


@router.post("/review-items/{item_id}/claim", status_code=204)
async def claim(item_id: UUID, conn: ConnDep, supervisor: SupervisorDep) -> None:
    await supervision.claim(conn, item_id, supervisor.id)


class Resolution(BaseModel):
    resolution: str = Field(min_length=3, max_length=1000)


@router.post("/review-items/{item_id}/resolve", status_code=204)
async def resolve(
    item_id: UUID, body: Resolution, conn: ConnDep, supervisor: SupervisorDep
) -> None:
    await supervision.resolve(conn, item_id, supervisor.id, body.resolution)


# Pickups -----------------------------------------------------------------------


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class Assignment(BaseModel):
    rider_id: UUID


@router.post("/pickups/{pickup_id}/reissue-pin", status_code=202)
async def reissue_pin(pickup_id: UUID, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    await _found(supervision.reissue_pin(conn, pickup_id, supervisor.id))
    return {"status": "a new PIN is on its way to the household"}


@router.post("/pickups/{pickup_id}/cancel")
async def cancel(pickup_id: UUID, body: Reason, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    refunding = await _found(supervision.cancel(conn, pickup_id, supervisor.id, body.reason))
    return {"status": "cancelled", "refund_started": refunding}


@router.post("/pickups/{pickup_id}/redispatch", status_code=202)
async def redispatch(pickup_id: UUID, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    await _found(supervision.redispatch(conn, pickup_id, supervisor.id))
    return {"status": "looking for another rider"}


@router.post("/pickups/{pickup_id}/assign")
async def assign(
    pickup_id: UUID, body: Assignment, conn: ConnDep, supervisor: SupervisorDep
) -> dict:
    await _found(supervision.assign(conn, pickup_id, body.rider_id, supervisor.id))
    return {"status": "assigned"}


# Riders and payouts --------------------------------------------------------------


@router.get("/riders")
async def riders(conn: ConnDep, supervisor: SupervisorDep) -> list[dict]:
    return await supervision.list_riders(conn)


class RiderStatus(BaseModel):
    active: bool


@router.put("/riders/{rider_id}/status", status_code=204)
async def rider_status(
    rider_id: UUID, body: RiderStatus, conn: ConnDep, supervisor: SupervisorDep
) -> None:
    await supervision.set_rider_status(
        conn, rider_id, active=body.active, supervisor_id=supervisor.id
    )


@router.get("/riders/{rider_id}/payable")
async def rider_payable(rider_id: UUID, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    return {"payable_pesewas": await supervision.payable_now(conn, rider_id)}


class PayoutNumber(BaseModel):
    phone: str


@router.put("/riders/{rider_id}/payout-number", status_code=204)
async def payout_number(
    rider_id: UUID, body: PayoutNumber, conn: ConnDep, supervisor: SupervisorDep
) -> None:
    try:
        phone = people.require_ghana(people.normalize_msisdn(body.phone))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    await supervision.set_payout_number(conn, rider_id, phone, supervisor_id=supervisor.id)


class MomoReference(BaseModel):
    momo_reference: str = Field(max_length=100)

    @field_validator("momo_reference")
    @classmethod
    def _clean(cls, value: str) -> str:
        return supervision.clean_reference(value)


class NewPayout(BaseModel):
    amount_pesewas: int = Field(gt=0)
    momo_reference: str | None = Field(
        None, max_length=100, description="Set only if the money has already been sent"
    )

    @field_validator("momo_reference")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return None if value is None else supervision.clean_reference(value)


@router.post("/riders/{rider_id}/payouts", status_code=201)
async def request_payout(
    rider_id: UUID, body: NewPayout, conn: ConnDep, supervisor: SupervisorDep
) -> dict:
    result = await supervision.request_payout(
        conn,
        rider_id,
        amount=body.amount_pesewas,
        supervisor_id=supervisor.id,
        momo_reference=body.momo_reference,
    )
    return {"payout_id": result.payout_id, "status": result.status}


@router.get("/payouts")
async def payouts(
    conn: ConnDep,
    supervisor: SupervisorDep,
    status_: Literal["pending_approval", "approved", "recorded", "rejected"] = Query(
        "pending_approval", alias="status"
    ),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    return await supervision.list_payouts(conn, status=status_, limit=limit)


@router.post("/payouts/{payout_id}/approve", status_code=204)
async def approve_payout(payout_id: UUID, conn: ConnDep, supervisor: SupervisorDep) -> None:
    await _found(supervision.approve_payout(conn, payout_id, supervisor.id), "payout")


@router.post("/payouts/{payout_id}/reject", status_code=204)
async def reject_payout(
    payout_id: UUID, body: Reason, conn: ConnDep, supervisor: SupervisorDep
) -> None:
    await _found(supervision.reject_payout(conn, payout_id, supervisor.id, body.reason), "payout")


@router.post("/payouts/{payout_id}/record", status_code=204)
async def record_payout(
    payout_id: UUID, body: MomoReference, conn: ConnDep, supervisor: SupervisorDep
) -> None:
    await _found(
        supervision.record_payout(
            conn, payout_id, momo_reference=body.momo_reference, supervisor_id=supervisor.id
        ),
        "payout",
    )


# Zones and landmarks -------------------------------------------------------------


class Place(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class NewZone(Place):
    radius_m: int = Field(gt=0, le=10000)


@router.get("/zones")
async def zones(conn: ConnDep, supervisor: SupervisorDep) -> list[dict]:
    return await supervision.list_zones(conn)


@router.post("/zones", status_code=201)
async def create_zone(body: NewZone, conn: ConnDep, supervisor: SupervisorDep) -> dict:
    try:
        zone_id = await supervision.create_zone(
            conn,
            name=body.name,
            lat=body.lat,
            lng=body.lng,
            radius_m=body.radius_m,
            supervisor_id=supervisor.id,
        )
    except errors.UniqueViolation as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="a zone has this name") from exc
    except errors.CheckViolation as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="no more zones fit the menu") from exc
    return {"zone_id": zone_id}


@router.post("/zones/{zone_id}/landmarks", status_code=201)
async def add_landmark(
    body: Place,
    conn: ConnDep,
    supervisor: SupervisorDep,
    zone_id: int = Path(ge=1, le=32767),
) -> dict:
    try:
        landmark_id = await _found(
            supervision.add_landmark(
                conn,
                zone_id,
                name=body.name,
                lat=body.lat,
                lng=body.lng,
                supervisor_id=supervisor.id,
            ),
            "zone",
        )
    except errors.CheckViolation as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="no more landmarks fit this zone's menu"
        ) from exc
    return {"landmark_id": landmark_id}


async def _found(action, what: str = "pickup"):
    try:
        return await action
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found") from exc

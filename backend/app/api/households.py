from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from psycopg import errors
from pydantic import BaseModel, Field

from app.adapters.base import Network
from app.api import idempotency
from app.api.common import PinEntry, pin_response
from app.api.deps import HouseholdBriefDep, HouseholdDep, PrincipalDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import payments, people, pickup_flow, pins
from app.domain.pickups import Offering

router = APIRouter(prefix="/v1", tags=["households"])


class HouseholdProfile(BaseModel):
    consent_version: str = Field(description="The consent notice version the household accepted")
    name: str | None = Field(default=None, max_length=100)
    address_text: str | None = Field(default=None, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


@router.put("/households/me")
async def register(body: HouseholdProfile, conn: ConnDep, principal: PrincipalDep) -> dict:
    if body.consent_version != get_settings().consent_version:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"accept consent notice {get_settings().consent_version} first",
        )
    if not principal.phone:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="sign in with a phone number")
    try:
        phone = people.require_ghana(people.normalize_msisdn(principal.phone))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    try:
        user, household_id = await people.register_household(
            conn,
            auth_user_id=principal.auth_user_id,
            phone_e164=phone,
            name=body.name,
            consent_version=body.consent_version,
            lat=body.lat,
            lng=body.lng,
            address_text=body.address_text,
        )
    except errors.UniqueViolation as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="this phone number is already registered"
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"user_id": user.id, "household_id": household_id}


class PickupRequest(BaseModel):
    offering: Literal["refuse"] = "refuse"
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class PickupView(BaseModel):
    id: UUID
    offering: str
    status: str
    fee_pesewas: int | None
    requested_at: datetime
    completed_at: datetime | None


@router.post("/pickups", status_code=201)
async def request_pickup(
    body: PickupRequest,
    conn: ConnDep,
    household: HouseholdDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    async def work() -> tuple[int, dict]:
        try:
            pickup_id = await pickup_flow.create(
                conn,
                household_id=household.household_id,
                user_id=household.user.id,
                offering=Offering(body.offering),
                lat=body.lat,
                lng=body.lng,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return 201, (await _view(conn, pickup_id, household.household_id)).model_dump(mode="json")

    code, view = await idempotency.once(
        conn,
        user_id=household.user.id,
        key=idempotency_key,
        request=body.model_dump(),
        work=work,
    )
    return JSONResponse(status_code=code, content=view)


@router.get("/pickups")
async def my_pickups(conn: ConnDep, household: HouseholdDep) -> list[PickupView]:
    cur = await conn.execute(
        """
        select id, offering, status, fee_pesewas, requested_at, completed_at
          from engine.pickup_requests
         where household_id = %s
         order by requested_at desc limit 50
        """,
        (household.household_id,),
    )
    return [PickupView(**row) for row in await cur.fetchall()]


@router.get("/pickups/{pickup_id}")
async def my_pickup(pickup_id: UUID, conn: ConnDep, household: HouseholdDep) -> PickupView:
    return await _view(conn, pickup_id, household.household_id)


class PaymentStart(BaseModel):
    network: Network


class PaymentStarted(BaseModel):
    reference: str
    state: str
    display_text: str | None
    needs_otp: bool


@router.post("/pickups/{pickup_id}/payment")
async def pay(
    pickup_id: UUID, body: PaymentStart, request: Request, household: HouseholdBriefDep
) -> PaymentStarted:
    try:
        started = await payments.start_payment(
            request.app.state.pool,
            pickup_id=pickup_id,
            household_user_id=household.user.id,
            network=body.network,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="pickup not found") from exc
    return PaymentStarted(
        reference=started.reference,
        state=started.state,
        display_text=started.display_text,
        needs_otp=started.needs_otp,
    )


class PaymentOtp(BaseModel):
    reference: str
    otp: str = Field(min_length=3, max_length=12)


@router.post("/pickups/{pickup_id}/payment/otp")
async def pay_otp(
    pickup_id: UUID, body: PaymentOtp, request: Request, household: HouseholdBriefDep
) -> PaymentStarted:
    try:
        started = await payments.submit_otp(
            request.app.state.pool,
            pickup_id=pickup_id,
            household_id=household.household_id,
            reference=body.reference,
            otp=body.otp,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="payment not found") from exc
    return PaymentStarted(
        reference=started.reference,
        state=started.state,
        display_text=started.display_text,
        needs_otp=started.needs_otp,
    )


@router.post("/pickups/{pickup_id}/cancel")
async def cancel(pickup_id: UUID, conn: ConnDep, household: HouseholdDep) -> dict:
    refunding = await pickup_flow.cancel(
        conn, pickup_id, household_id=household.household_id, user_id=household.user.id
    )
    return {"status": "cancelled", "refund_started": refunding}


@router.post("/pickups/{pickup_id}/pin/resend", status_code=202)
async def resend_pin(pickup_id: UUID, conn: ConnDep, household: HouseholdDep) -> dict:
    await pins.request_resend(conn, pickup_id, household_id=household.household_id)
    return {"status": "a new PIN is on its way"}


@router.post("/pickups/{pickup_id}/confirm")
async def confirm(
    pickup_id: UUID, body: PinEntry, conn: ConnDep, household: HouseholdDep
) -> JSONResponse:
    await _view(conn, pickup_id, household.household_id)
    result = await pins.confirm(
        conn,
        pickup_id,
        body.pin,
        via="household_pwa",
        actor_type="household",
        actor_user_id=household.user.id,
    )
    return pin_response(result)


async def _view(conn: ConnDep, pickup_id: UUID, household_id: UUID) -> PickupView:
    cur = await conn.execute(
        """
        select id, offering, status, fee_pesewas, requested_at, completed_at
          from engine.pickup_requests where id = %s and household_id = %s
        """,
        (pickup_id, household_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="pickup not found")
    return PickupView(**row)

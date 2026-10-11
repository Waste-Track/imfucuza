from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from psycopg import errors
from pydantic import BaseModel, Field

from app.adapters.base import Network
from app.api import idempotency, uploads
from app.api.common import PinEntry, pin_response
from app.api.deps import HouseholdBriefDep, HouseholdDep, PrincipalDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import payments, people, pickup_flow, pins, plastic, rewards
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
    offering: Literal["refuse", "plastic"] = "refuse"
    # Plastic only: what the household says it is handing over.
    declared_type: Literal["pet", "hdpe", "pp", "mixed_plastic"] | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class PickupView(BaseModel):
    id: UUID
    offering: str
    status: str
    fee_pesewas: int | None
    declared_type: str | None
    weight_g: int | None
    points_due: int | None
    verification_outcome: str
    # The rider has weighed the plastic and, on a basic phone, needs the
    # household to send the photo.
    photo_wanted: bool
    requested_at: datetime
    completed_at: datetime | None


_VIEW = """
    p.id, p.offering, p.status, p.fee_pesewas, p.declared_type, p.weight_g, p.points_due,
    p.verification_outcome, p.requested_at, p.completed_at,
    (p.offering = 'plastic' and p.status = 'arrived' and p.weight_g is not null
     and coalesce(r.channel = 'ussd', false)) as photo_wanted
    from engine.pickup_requests p
    left join engine.riders r on r.id = p.assigned_rider_id
"""


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
                declared_type=body.declared_type,
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
        select """
        + _VIEW
        + """
         where p.household_id = %s
         order by p.requested_at desc limit 50
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
        select """
        + _VIEW
        + """
         where p.id = %s and p.household_id = %s
        """,
        (pickup_id, household_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="pickup not found")
    return PickupView(**row)


class Points(BaseModel):
    available: int
    # Earned on plastic a supervisor hasn't checked yet.
    pending: int


@router.get("/points/me")
async def my_points(conn: ConnDep, household: HouseholdDep) -> Points:
    return Points(**await plastic.balance(conn, household.household_id))


@router.post("/pickups/{pickup_id}/plastic/photo")
async def send_plastic_photo(
    pickup_id: UUID,
    request: Request,
    household: HouseholdBriefDep,
    weight_g: Annotated[int, Query(gt=0, description="The weight the app showed the household")],
) -> dict:
    """When the rider has a basic phone, the household photographs the plastic
    once it has been weighed. The photo is the request body."""
    pool = request.app.state.pool
    async with pool.connection() as conn, conn.transaction():
        await plastic.expect_household_photo(conn, pickup_id, household.household_id, weight_g)
    photo = await uploads.store(request, pickup_id)
    async with uploads.kept_if_recorded(photo), pool.connection() as conn, conn.transaction():
        result = await plastic.submit_from_household(
            conn,
            pickup_id,
            household_id=household.household_id,
            user_id=household.user.id,
            photo_id=photo.id,
            storage_key=photo.storage_key,
            photo=photo.prepared,
            weight_g=weight_g,
        )
    return {"status": result.status}


# Rewards ------------------------------------------------------------------------


class Product(BaseModel):
    id: UUID
    name: str
    points_cost: int
    face_value_pesewas: int


@router.get("/vouchers/catalog")
async def voucher_catalogue(conn: ConnDep, household: HouseholdDep) -> list[Product]:
    return [Product(**row) for row in await rewards.catalogue(conn)]


class VoucherRequest(BaseModel):
    product_id: UUID


class Voucher(BaseModel):
    id: UUID
    product: str
    points: int
    status: str
    # Only while it can still be used.
    code: str | None
    issued_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_at_shop: str | None


@router.post("/vouchers", status_code=201)
async def get_voucher(
    body: VoucherRequest,
    conn: ConnDep,
    household: HouseholdDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Spend points on a voucher. Its code comes by SMS and in GET /vouchers/me."""

    async def work() -> tuple[int, dict]:
        issued = await rewards.issue(
            conn, household.household_id, body.product_id, household.user.id
        )
        # Not kept with the idempotency record: the code comes by SMS and from
        # GET /vouchers/me, which stops showing it once the voucher is used.
        return 201, Voucher(**{**issued, "code": None}).model_dump(mode="json")

    code, view = await idempotency.once(
        conn,
        user_id=household.user.id,
        key=idempotency_key,
        request=body.model_dump(mode="json"),
        work=work,
    )
    return JSONResponse(status_code=code, content=view)


@router.get("/vouchers/me")
async def my_vouchers(conn: ConnDep, household: HouseholdDep) -> list[Voucher]:
    return [
        Voucher(**row) for row in await rewards.household_vouchers(conn, household.household_id)
    ]


class LeaderboardChoice(BaseModel):
    # A name to show, or null to leave the board.
    name: str | None = Field(default=None, max_length=40)
    consent_version: str | None = Field(
        default=None, description="The leaderboard notice version accepted, when joining"
    )


@router.put("/households/me/leaderboard", status_code=204)
async def leaderboard_choice(
    body: LeaderboardChoice, conn: ConnDep, household: HouseholdDep
) -> None:
    if body.name is not None and body.consent_version != get_settings().leaderboard_consent_version:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"accept leaderboard notice {get_settings().leaderboard_consent_version} first",
        )
    try:
        name = None if body.name is None else rewards.clean_name(body.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    await rewards.set_leaderboard_name(
        conn,
        household.household_id,
        name,
        user_id=household.user.id,
        consent_version=body.consent_version,
        channel="pwa",
    )


@router.get("/leaderboard")
async def leaderboard(
    conn: ConnDep,
    household: HouseholdDep,
    month: Annotated[str | None, Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")] = None,
) -> dict:
    """The board for the household's own zone, this month unless told otherwise."""
    when = date.fromisoformat(f"{month}-01") if month else datetime.now(UTC).date().replace(day=1)
    board = await rewards.leaderboard(conn, household.household_id, month=when)
    if board is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no zone found for this household")
    return board

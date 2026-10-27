from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import PrincipalDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import people

router = APIRouter(prefix="/v1", tags=["account"])


class Me(BaseModel):
    # None until the signed-in phone registers as a household or a supervisor
    # registers it as a rider.
    role: str | None
    name: str | None
    status: str | None
    household_id: UUID | None
    rider_id: UUID | None
    # Riders only.
    on_duty: bool | None
    # Notice versions a client must show before registering or joining the board.
    consent_version: str
    leaderboard_consent_version: str
    refuse_fee_pesewas: int


@router.get("/me")
async def me(conn: ConnDep, principal: PrincipalDep) -> Me:
    """Who the signed-in phone is, so an app can open the right screens."""
    settings = get_settings()
    user = await people.find_signed_in_user(conn, principal.auth_user_id, principal.phone)
    household_id = rider_id = on_duty = None
    if user and user.role == "household":
        household_id = await people.household_id_for(conn, user.id)
    elif user and user.role == "rider":
        cur = await conn.execute(
            "select id, on_duty from engine.riders where user_id = %s", (user.id,)
        )
        row = await cur.fetchone()
        if row:
            rider_id, on_duty = row["id"], row["on_duty"]
    return Me(
        role=user.role if user else None,
        name=user.name if user else None,
        status=user.status if user else None,
        household_id=household_id,
        rider_id=rider_id,
        on_duty=on_duty,
        consent_version=settings.consent_version,
        leaderboard_consent_version=settings.leaderboard_consent_version,
        refuse_fee_pesewas=settings.refuse_fee_pesewas,
    )

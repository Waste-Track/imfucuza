from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from app.auth import Principal, current_principal
from app.db import ConnDep
from app.domain import people
from app.domain.people import User

PrincipalDep = Annotated[Principal, Depends(current_principal)]


async def _signed_in_user(conn: ConnDep, principal: PrincipalDep) -> User:
    return await _resolve_user(conn, principal)


async def _resolve_user(conn, principal: Principal) -> User:
    user = await people.find_signed_in_user(conn, principal.auth_user_id, principal.phone)
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="complete registration first")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="account is not active")
    if not user.consented:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="consent is required")
    return user


UserDep = Annotated[User, Depends(_signed_in_user)]


@dataclass(frozen=True)
class Household:
    user: User
    household_id: UUID


@dataclass(frozen=True)
class Rider:
    user: User
    rider_id: UUID


async def _household(conn: ConnDep, user: UserDep) -> Household:
    household_id = (
        await people.household_id_for(conn, user.id) if user.role == "household" else None
    )
    if household_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="households only")
    return Household(user, household_id)


async def _rider(conn: ConnDep, user: UserDep) -> Rider:
    rider_id = await people.rider_id_for(conn, user.id) if user.role == "rider" else None
    if rider_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="riders only")
    return Rider(user, rider_id)


def _supervisor(user: UserDep) -> User:
    if user.role not in ("supervisor", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="supervisors only")
    return user


async def _household_brief(request: Request, principal: PrincipalDep) -> Household:
    """Like HouseholdDep, but gives its connection back at once: for routes that
    then wait on a provider and must not hold a pool connection meanwhile."""
    async with request.app.state.pool.connection() as conn, conn.transaction():
        user = await _resolve_user(conn, principal)
        return await _household(conn, user)


HouseholdDep = Annotated[Household, Depends(_household)]
HouseholdBriefDep = Annotated[Household, Depends(_household_brief)]
RiderDep = Annotated[Rider, Depends(_rider)]
SupervisorDep = Annotated[User, Depends(_supervisor)]

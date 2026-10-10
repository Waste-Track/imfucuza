from typing import Literal

from fastapi import APIRouter, HTTPException, status
from psycopg import errors
from pydantic import BaseModel, Field

from app.api.deps import SupervisorDep
from app.config import get_settings
from app.db import ConnDep
from app.domain import people

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

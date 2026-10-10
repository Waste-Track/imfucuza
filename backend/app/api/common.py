from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain import pins


class PinEntry(BaseModel):
    pin: str = Field(pattern=r"^[0-9]{6}$")


def pin_response(result: pins.PinResult) -> JSONResponse:
    if result.confirmed:
        return JSONResponse({"status": "completed"})
    # Returned, not raised, so the failed attempt is committed.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": "wrong PIN", "tries_left": result.tries_left, "locked": result.locked},
    )

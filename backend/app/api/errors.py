"""Domain exceptions as HTTP responses. Raising rolls the request's
transaction back, so nothing a failed request did is kept."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import errors as pg

from app.adapters.base import ProviderError
from app.domain.dispatch import (
    OfferNotAvailable,
    OfferNotFound,
    SelfReportNotAllowed,
    TooManyLocationChanges,
)
from app.domain.payments import PaymentNotAllowed
from app.domain.pickup_flow import NotYourPickup, TooFarAway, TooSoon
from app.domain.pickups import InvalidTransition, StaleVersion
from app.domain.pins import PinLocked, PinUnavailable, TooManyAttempts
from app.domain.supervision import PayoutNotAllowed, ReviewItemUnavailable, RiderUnavailable

_STATUS: list[tuple[type[Exception], int]] = [
    (NotYourPickup, 404),
    (OfferNotFound, 404),
    (InvalidTransition, 409),
    (StaleVersion, 409),
    (OfferNotAvailable, 409),
    (PaymentNotAllowed, 409),
    (PinUnavailable, 409),
    (PinLocked, 423),
    (TooManyAttempts, 429),
    (TooFarAway, 422),
    (TooSoon, 422),
    (SelfReportNotAllowed, 403),
    (TooManyLocationChanges, 429),
    (ReviewItemUnavailable, 409),
    (PayoutNotAllowed, 409),
    (RiderUnavailable, 409),
    (ProviderError, 502),
    # Two requests raced for the same rows. The loser can simply try again.
    (pg.DeadlockDetected, 409),
    (pg.SerializationFailure, 409),
]


def install(app: FastAPI) -> None:
    for exc_type, status in _STATUS:
        app.add_exception_handler(exc_type, _handler(status))


def _handler(status: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        if status == 502:
            detail = "payment provider unavailable, try again"
        elif isinstance(exc, pg.Error):
            detail = "the request clashed with another one, try again"
        else:
            detail = str(exc)
        return JSONResponse(status_code=status, content={"detail": detail})

    return handle

"""Pickup photos sent as a request body, for the rider's and the household's
upload routes.

No database connection is held while a photo uploads and is processed: slow
uploads would otherwise starve every other request."""

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
from fastapi import HTTPException, Request, status

from app import services
from app.adapters.base import ProviderError
from app.domain import photos


@dataclass(frozen=True)
class StoredPhoto:
    id: UUID
    storage_key: str
    prepared: photos.PreparedPhoto


async def store(request: Request, pickup_id: UUID) -> StoredPhoto:
    """Read the body with a size cap, re-encode it off the event loop, and store it."""
    data = await _read(request, photos.MAX_UPLOAD_BYTES)
    prepared = await anyio.to_thread.run_sync(
        photos.prepare, data, limiter=request.app.state.photo_limiter
    )
    photo_id = uuid4()
    storage_key = f"{pickup_id}/{photo_id}.jpg"
    try:
        await services.get().photos.put(storage_key, prepared.jpeg, "image/jpeg")
    except ProviderError as exc:
        if exc.retryable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="the photo couldn't be stored, try again",
            ) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="the photo couldn't be stored"
        ) from exc
    return StoredPhoto(photo_id, storage_key, prepared)


@contextlib.asynccontextmanager
async def kept_if_recorded(photo: StoredPhoto) -> AsyncIterator[None]:
    """Delete the stored photo if recording it fails: with no record it could
    never be found or deleted again."""
    try:
        yield
    except BaseException:
        with anyio.CancelScope(shield=True), contextlib.suppress(ProviderError):
            await services.get().photos.delete(photo.storage_key)
        raise


async def _read(request: Request, limit: int) -> bytes:
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

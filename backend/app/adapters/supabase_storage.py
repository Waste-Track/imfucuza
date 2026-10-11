"""Pickup photos in a private Supabase Storage bucket, over its HTTP API.

The project's secret key goes in the `apikey` header only. It isn't a JWT, so
it must never be sent as a Bearer token.
"""

from typing import Any
from urllib.parse import quote

import httpx

from app.adapters.base import ProviderError

TIMEOUT_S = 15.0


class SupabasePhotoStore:
    name = "supabase"

    def __init__(
        self,
        supabase_url: str,
        secret_key: str,
        bucket: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not secret_key:
            raise ValueError("Supabase Storage needs the project's secret key")
        self._base_url = f"{supabase_url.rstrip('/')}/storage/v1"
        self._headers = {"apikey": secret_key}
        self._bucket = quote(bucket, safe="")
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        # Upsert, so a retry after a lost reply rewrites the same bytes. Keys
        # are unique per upload, so it never replaces another photo.
        await self._call(
            "POST",
            f"/object/{self._bucket}/{quote(key)}",
            content=data,
            headers={"content-type": content_type, "x-upsert": "true"},
        )

    async def signed_url(self, key: str, ttl_s: int) -> str:
        body = await self._call(
            "POST", f"/object/sign/{self._bucket}/{quote(key)}", json={"expiresIn": ttl_s}
        )
        path = body.get("signedURL")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ProviderError("supabase storage: no signed URL in the reply")
        return self._base_url + path

    async def _call(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        where = f"supabase storage {method} {path}"
        try:
            response = await self._client.request(
                method,
                self._base_url + path,
                headers={**self._headers, **(headers or {})},
                timeout=TIMEOUT_S,
                **kwargs,
            )
        except httpx.TimeoutException:
            raise ProviderError(f"{where}: timed out") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"{where}: {type(exc).__name__}") from None

        try:
            body = response.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        if response.status_code >= 400:
            message = body.get("message") or body.get("error") or ""
            detail = f"{where}: HTTP {response.status_code} {message}".rstrip()
            retryable = response.status_code >= 500 or response.status_code == 429
            raise ProviderError(detail, retryable=retryable)
        return body

from collections.abc import Callable

import httpx
import pytest

from app.adapters.base import ProviderError
from app.adapters.supabase_storage import SupabasePhotoStore

pytestmark = pytest.mark.anyio

KEY = "sb_secret_dummy-for-tests"
Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


def store(reply: Reply) -> tuple[SupabasePhotoStore, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return reply(request) if callable(reply) else reply

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabasePhotoStore(
        "https://proj.supabase.co/", KEY, "pickup-photos", client=client
    ), requests


async def test_put_uploads_to_the_private_bucket_with_the_key_in_apikey_only():
    photos, requests = store(httpx.Response(200, json={"Key": "pickup-photos/p/1.jpg"}))

    await photos.put("p/1.jpg", b"jpeg-bytes", "image/jpeg")

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "https://proj.supabase.co/storage/v1/object/pickup-photos/p/1.jpg"
    assert request.headers["apikey"] == KEY
    assert "authorization" not in request.headers
    assert request.headers["content-type"] == "image/jpeg"
    assert request.headers["x-upsert"] == "true"
    assert request.content == b"jpeg-bytes"


async def test_signed_url_is_absolute_and_short_lived():
    photos, requests = store(
        httpx.Response(200, json={"signedURL": "/object/sign/pickup-photos/p/1.jpg?token=abc"})
    )

    url = await photos.signed_url("p/1.jpg", 300)

    assert url == "https://proj.supabase.co/storage/v1/object/sign/pickup-photos/p/1.jpg?token=abc"
    assert requests[0].read() == b'{"expiresIn":300}'


@pytest.mark.parametrize(
    ("reply", "retryable"),
    [
        (httpx.Response(503, json={"message": "unavailable"}), True),
        (httpx.Response(429), True),
        (httpx.Response(400, json={"error": "Invalid key"}), False),
        (httpx.Response(403, json={"message": "new row violates row-level security"}), False),
    ],
)
async def test_failed_upload_raises_with_retry_advice(reply, retryable):
    photos, _ = store(reply)

    with pytest.raises(ProviderError) as exc:
        await photos.put("p/1.jpg", b"x", "image/jpeg")

    assert exc.value.retryable is retryable
    assert KEY not in str(exc.value)


async def test_unreachable_storage_is_retryable_and_hides_the_key():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect, apikey={KEY}")

    photos, _ = store(boom)

    with pytest.raises(ProviderError) as exc:
        await photos.signed_url("p/1.jpg", 300)

    assert exc.value.retryable
    assert KEY not in str(exc.value)


async def test_signed_url_reply_without_a_url_is_an_error():
    photos, _ = store(httpx.Response(200, json={"error": None}))

    with pytest.raises(ProviderError):
        await photos.signed_url("p/1.jpg", 300)


def test_a_secret_key_is_required():
    with pytest.raises(ValueError):
        SupabasePhotoStore("https://proj.supabase.co", "", "pickup-photos")

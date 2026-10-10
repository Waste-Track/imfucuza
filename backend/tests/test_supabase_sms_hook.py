import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest

SECRET_BYTES = b"supabase-hook-secret-for-tests!!"
SECRET = "v1,whsec_" + base64.b64encode(SECRET_BYTES).decode()


@pytest.fixture
def hooked(world, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("SUPABASE_SMS_HOOK_SECRET", SECRET)
    get_settings.cache_clear()
    return world


def signed(body: bytes, *, at: int | None = None, key: bytes = SECRET_BYTES) -> dict:
    msg_id, timestamp = f"msg_{uuid4().hex}", str(at or int(time.time()))
    digest = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
        "content-type": "application/json",
    }


BODY = json.dumps({"user": {"phone": "233241234567"}, "sms": {"otp": "561166"}}).encode()


def test_signed_hook_sends_the_code_by_sms(hooked):
    response = hooked.client.post("/hooks/supabase/send-sms", content=BODY, headers=signed(BODY))

    assert response.status_code == 200
    assert "561166" in hooked.sms.messages_to("+233241234567")[0]


def test_a_retry_after_a_failed_send_still_sends_the_code(hooked):
    headers = signed(BODY)
    hooked.sms.fail_next_send = True

    first = hooked.client.post("/hooks/supabase/send-sms", content=BODY, headers=headers)
    retry = hooked.client.post("/hooks/supabase/send-sms", content=BODY, headers=headers)
    replay = hooked.client.post("/hooks/supabase/send-sms", content=BODY, headers=headers)

    assert (first.status_code, retry.status_code, replay.status_code) == (502, 200, 200)
    assert len(hooked.sms.messages_to("+233241234567")) == 1


@pytest.mark.parametrize(
    "headers",
    [
        signed(BODY, key=b"some-other-secret-entirely-0000!"),
        signed(BODY, at=int(time.time()) - 3600),
        {"content-type": "application/json"},
    ],
    ids=["wrong-key", "stale", "unsigned"],
)
def test_unsigned_forged_or_stale_hooks_send_nothing(hooked, headers):
    response = hooked.client.post("/hooks/supabase/send-sms", content=BODY, headers=headers)

    assert response.status_code == 401
    assert hooked.sms.outbox == []

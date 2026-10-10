import pytest
from pydantic import SecretStr

from app.config import Settings


def test_local_settings_need_no_configuration():
    Settings(environment="local").check_deployable()


def test_deployed_engine_refuses_to_start_unconfigured():
    with pytest.raises(RuntimeError) as exc:
        Settings(environment="staging").check_deployable()

    assert {"DATABASE_URL", "INTERNAL_SECRET", "SUPABASE_URL", "PIN_PEPPER"} <= set(
        str(exc.value).split(": set ")[1].split(", ")
    )


def test_deployed_engine_starts_when_configured():
    Settings(
        environment="staging",
        database_url="postgresql://engine@db.example.supabase.co:5432/postgres",
        supabase_url="https://example.supabase.co",
        internal_secret=SecretStr("cron-secret"),
        pin_pepper=SecretStr("a-long-random-pepper"),
        payment_provider="paystack",
        paystack_secret_key=SecretStr("sk_test_x"),
        sms_gateway="mnotify",
        mnotify_api_key=SecretStr("mnotify-key"),
        supabase_sms_hook_secret=SecretStr("v1,whsec_c2VjcmV0"),
    ).check_deployable()


def test_deployed_engine_refuses_fake_providers():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER=paystack"):
        Settings(
            environment="staging",
            database_url="postgresql://engine@db.example.supabase.co:5432/postgres",
            supabase_url="https://example.supabase.co",
            internal_secret=SecretStr("cron-secret"),
            pin_pepper=SecretStr("a-long-random-pepper"),
        ).check_deployable()


def test_deployed_ussd_needs_a_long_token_and_a_code():
    with pytest.raises(RuntimeError) as exc:
        Settings(
            environment="staging",
            ussd_provider="africastalking",
            ussd_webhook_token=SecretStr("short"),
        ).check_deployable()

    assert "USSD_WEBHOOK_TOKEN (32+ characters)" in str(exc.value)
    assert "USSD_CODE" in str(exc.value)

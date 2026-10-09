import pytest
from pydantic import SecretStr

from app.config import Settings


def test_local_settings_need_no_configuration():
    Settings(environment="local").check_deployable()


def test_deployed_engine_refuses_to_start_unconfigured():
    with pytest.raises(RuntimeError) as exc:
        Settings(environment="staging").check_deployable()

    assert {"DATABASE_URL", "INTERNAL_SECRET", "SUPABASE_URL"} <= set(
        str(exc.value).split(": set ")[1].split(", ")
    )


def test_deployed_engine_starts_when_configured():
    Settings(
        environment="staging",
        database_url="postgresql://engine@db.example.supabase.co:5432/postgres",
        supabase_url="https://example.supabase.co",
        internal_secret=SecretStr("cron-secret"),
    ).check_deployable()

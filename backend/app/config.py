from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "local" allows the defaults below. Anything else must configure them.
    environment: str = "local"

    # Local Supabase defaults, so tests and `uvicorn --reload` work with no setup.
    database_url: str = LOCAL_DATABASE_URL
    supabase_url: str = "http://127.0.0.1:54321"
    jwt_audience: str = "authenticated"

    # Shared secret for /internal/* routes. Empty disables them.
    internal_secret: SecretStr = SecretStr("")

    db_pool_min_size: int = 1
    db_pool_max_size: int = 5
    jobs_per_tick: int = 25

    def check_deployable(self) -> None:
        """Refuse to start a deployed Engine that would silently do nothing:
        without the internal secret no timers run, so holds are never refunded."""
        if self.environment == "local":
            return
        missing = []
        if self.database_url == LOCAL_DATABASE_URL:
            missing.append("DATABASE_URL")
        if not self.internal_secret.get_secret_value():
            missing.append("INTERNAL_SECRET")
        if "127.0.0.1" in self.supabase_url or "localhost" in self.supabase_url:
            missing.append("SUPABASE_URL")
        if missing:
            raise RuntimeError(f"{self.environment}: set {', '.join(missing)}")

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()

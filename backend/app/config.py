from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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

    # Providers. "fake" keeps everything in memory, for tests and local work.
    payment_provider: Literal["fake", "paystack"] = "fake"
    paystack_secret_key: SecretStr = SecretStr("")
    sms_gateway: Literal["fake", "mnotify"] = "fake"
    mnotify_api_key: SecretStr = SecretStr("")
    sms_sender_id: str = "Imfucuza"
    # Placeholder payer emails for providers that require one: <user id>@<domain>.
    payer_email_domain: str = "payers.imfucuza.app"

    # USSD. The one provider whose callbacks are accepted ("" disables USSD),
    # the secret path segment it calls, and the code shown in SMS.
    ussd_provider: str = ""
    ussd_webhook_token: SecretStr = SecretStr("")
    ussd_code: str = ""
    # Optional addresses the provider calls from (CIDRs). Empty accepts any.
    ussd_allowed_ips: list[str] = []
    # Behind a proxy (Render), the caller is the last X-Forwarded-For entry.
    trust_forwarded_for: bool = False

    # Pickup photos: "supabase" keeps them in a private Storage bucket, read and
    # written with the project's secret key (sb_secret_...).
    photo_store: Literal["fake", "supabase"] = "fake"
    supabase_secret_key: SecretStr = SecretStr("")
    # The waste classifier. "none" sends every plastic pickup to a supervisor.
    classifier: Literal["fake", "http", "none"] = "fake"
    classifier_url: str = ""
    classifier_token: SecretStr = SecretStr("")
    # Plastic. Points per kg by declared type and what the platform pays the
    # rider per verified kg. The rider enters the weight and both sides gain
    # from inflating it, so loads above `plastic_review_above_g` wait for a
    # supervisor, and each household gets one open request and a daily cap.
    points_per_kg: dict[str, int] = {"pet": 10, "hdpe": 10, "pp": 10, "mixed_plastic": 5}
    rider_pay_pesewas_per_kg: int = Field(100, ge=0)
    plastic_max_weight_g: int = Field(25_000, gt=0)
    plastic_review_above_g: int = Field(10_000, gt=0)
    # Lower when the household took the photo for a feature-phone rider.
    plastic_household_review_above_g: int = Field(5_000, gt=0)
    plastic_pickups_per_day: int = Field(2, gt=0)
    # How long a rider has to decide on plastic the classifier was unsure about.
    rider_review_s: int = Field(10 * 60, gt=0)
    # Rewards. How long a voucher can be used for, and the version of the notice
    # households accept before their name appears on the leaderboard.
    voucher_valid_days: int = Field(30, gt=0)
    leaderboard_consent_version: str = "2026-10-lb1"
    # Decision policy (engine-design.md section 5). Starting values until pilot
    # data calibrates them. Change the version whenever a threshold changes.
    verification_policy_version: str = "2026-10-p1"
    verify_accept_at: float = Field(0.90, gt=0, le=1)
    verify_reject_at: float = Field(0.80, gt=0, le=1)

    # Version of the consent notice households and riders must accept (Act 843).
    consent_version: str = "2026-10-v1"
    # Supabase Auth "Send SMS" hook secret, "v1,whsec_...". Empty disables the hook.
    supabase_sms_hook_secret: SecretStr = SecretStr("")

    # Secret mixed into stored PIN hashes, so a database leak can't be brute-forced.
    pin_pepper: SecretStr = SecretStr("local-pin-pepper")

    # Pricing, in pesewas and percent. Starting values (engine-plan.md, open question 1).
    refuse_fee_pesewas: int = 1000
    rider_share_percent: int = 70

    # Timings (engine-design.md sections 1 to 3). Starting values.
    payment_window_s: int = 15 * 60
    dispatch_window_s: int = 45 * 60
    dispatch_retry_s: int = 3 * 60
    offer_ttl_pwa_s: int = 90
    offer_ttl_ussd_s: int = 5 * 60
    dispatch_radius_m: int = 5000
    gps_fresh_s: int = 10 * 60
    self_report_fresh_s: int = 60 * 60
    arrival_radius_m: int = 150
    pin_ttl_s: int = 24 * 60 * 60
    pin_max_attempts: int = 5

    # Rider earnings stay unpaid this long after release, so a dispute can still
    # reach them. Payouts to one rider above the limit in a day need a second supervisor.
    payout_hold_s: int = 48 * 60 * 60
    payout_second_approval_pesewas: int = 20000

    @field_validator("points_per_kg")
    @classmethod
    def _every_type_priced(cls, rates: dict[str, int]) -> dict[str, int]:
        types = {"pet", "hdpe", "pp", "mixed_plastic"}
        if set(rates) != types or any(rate < 0 for rate in rates.values()):
            raise ValueError(f"give a points rate of 0 or more for each of {sorted(types)}")
        return rates

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
        if self.pin_pepper.get_secret_value() == "local-pin-pepper":
            missing.append("PIN_PEPPER")
        # Fakes keep money and messages in memory: never outside local work.
        if self.payment_provider != "paystack":
            missing.append("PAYMENT_PROVIDER=paystack")
        if not self.paystack_secret_key.get_secret_value():
            missing.append("PAYSTACK_SECRET_KEY")
        if self.sms_gateway != "mnotify":
            missing.append("SMS_GATEWAY=mnotify")
        if not self.mnotify_api_key.get_secret_value():
            missing.append("MNOTIFY_API_KEY")
        if not self.supabase_sms_hook_secret.get_secret_value():
            missing.append("SUPABASE_SMS_HOOK_SECRET")
        if self.photo_store != "supabase":
            missing.append("PHOTO_STORE=supabase")
        if not self.supabase_secret_key.get_secret_value():
            missing.append("SUPABASE_SECRET_KEY")
        if self.classifier == "fake":
            missing.append("CLASSIFIER=http (or none)")
        if self.classifier == "http" and not (
            self.classifier_url.startswith("https://") and self.classifier_token.get_secret_value()
        ):
            missing.append("CLASSIFIER_URL (https) and CLASSIFIER_TOKEN")
        if self.ussd_provider:
            if len(self.ussd_webhook_token.get_secret_value()) < 32:
                missing.append("USSD_WEBHOOK_TOKEN (32+ characters)")
            if not self.ussd_code:
                missing.append("USSD_CODE")
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

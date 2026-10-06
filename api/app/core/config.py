"""Application settings, loaded from environment variables (and `.env` in development).

Every secret is a `SecretStr` so it never ends up in logs or reprs. Keys are only ever used
server-side; the web app talks exclusively to this API.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SESSION_SECRET = "dev-only-insecure-session-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # `KEY=` (as .env.example leaves unused keys) means "not set": use the default.
        env_ignore_empty=True,
    )

    # --- Runtime -------------------------------------------------------------------------------
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    market_timezone: str = "America/New_York"
    # Processes for pattern detection in the EOD scan: 0 = one per CPU core but one, 1 = none.
    pattern_workers: int = Field(0, ge=0, le=64)
    # Where backtest candidate tapes are cached (Parquet; safe to delete, rebuilt on demand).
    backtest_dir: str = "var/backtests"
    # The backup service's last.json (production: the backups volume, mounted read-only). When
    # set, /api/health/ready reports the last backup and fails it once it's older than the limit.
    backup_status_file: str | None = None
    backup_max_age_hours: int = Field(26, ge=1, le=24 * 14)

    # --- Infrastructure ------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://breakout:breakout@localhost:5432/breakout"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: SecretStr = SecretStr(DEV_SESSION_SECRET)

    # --- Provider selection (see providers/base.py) --------------------------------------------
    price_provider: Literal["yfinance", "massive"] = "yfinance"
    stream_provider: Literal["none", "alpaca", "replay"] = "none"
    fundamentals_provider: Literal["sec_edgar", "fmp"] = "sec_edgar"
    news_provider: Literal["none", "finnhub"] = "none"

    # --- Market data credentials ---------------------------------------------------------------
    massive_api_key: SecretStr | None = None
    massive_base_url: str = "https://api.massive.com"
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_feed: Literal["iex", "sip"] = "iex"
    # Replay mode (STREAM_PROVIDER=replay): a recorded session played back as the live feed.
    replay_file: str | None = None
    replay_speed: float = Field(60, ge=0)  # × real time; 0 = as fast as possible
    replay_start: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # At the end of a replay, store the day's bars and run the close (confirm or reject).
    replay_close: bool = True
    fmp_api_key: SecretStr | None = None
    finnhub_api_key: SecretStr | None = None
    sec_user_agent: str | None = None

    # --- AI narrative (optional) ---------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None

    # --- Notifications -------------------------------------------------------------------------
    # The web address alerts link to (stock pages, the alerts centre).
    public_url: str = "http://localhost:3000"
    email_provider: Literal["resend", "smtp"] = "resend"
    resend_api_key: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    email_from: str | None = None
    email_to: str | None = None
    # Set by docker-compose for development: with no email provider configured, alerts are
    # mailed to this SMTP catcher (Mailpit, web UI on :8025) instead of nowhere.
    mail_catcher_host: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None

    @model_validator(mode="after")
    def _require_real_secrets_in_prod(self) -> "Settings":
        if self.app_env == "prod" and self.session_secret.get_secret_value() == DEV_SESSION_SECRET:
            raise ValueError("SESSION_SECRET must be set to a strong random value in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

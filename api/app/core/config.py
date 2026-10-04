"""Application settings, loaded from environment variables (and `.env` in development).

Every secret is a `SecretStr` so it never ends up in logs or reprs. Keys are only ever used
server-side; the web app talks exclusively to this API.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SESSION_SECRET = "dev-only-insecure-session-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime -------------------------------------------------------------------------------
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    market_timezone: str = "America/New_York"

    # --- Infrastructure ------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://breakout:breakout@localhost:5432/breakout"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: SecretStr = SecretStr(DEV_SESSION_SECRET)

    # --- Provider selection (see providers/base.py) --------------------------------------------
    price_provider: Literal["yfinance", "massive"] = "yfinance"
    stream_provider: Literal["none", "alpaca"] = "none"
    fundamentals_provider: Literal["sec_edgar", "fmp"] = "sec_edgar"
    news_provider: Literal["none", "finnhub"] = "none"

    # --- Market data credentials ---------------------------------------------------------------
    massive_api_key: SecretStr | None = None
    massive_base_url: str = "https://api.massive.com"
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_feed: Literal["iex", "sip"] = "iex"
    fmp_api_key: SecretStr | None = None
    finnhub_api_key: SecretStr | None = None
    sec_user_agent: str | None = None

    # --- AI narrative (optional) ---------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None

    # --- Notifications -------------------------------------------------------------------------
    email_provider: Literal["resend", "smtp"] = "resend"
    resend_api_key: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str | None = None
    email_to: str | None = None
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

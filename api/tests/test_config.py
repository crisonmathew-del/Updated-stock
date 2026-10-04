import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import DEV_SESSION_SECRET, Settings


def test_defaults_need_no_api_keys() -> None:
    settings = Settings(_env_file=None, app_env="dev")
    assert settings.price_provider == "yfinance"
    assert settings.fundamentals_provider == "sec_edgar"
    assert settings.stream_provider == "none"
    assert settings.massive_api_key is None


def test_production_rejects_dev_session_secret() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(_env_file=None, app_env="prod", session_secret=SecretStr(DEV_SESSION_SECRET))


def test_production_accepts_real_session_secret() -> None:
    settings = Settings(_env_file=None, app_env="prod", session_secret=SecretStr("x" * 48))
    assert settings.app_env == "prod"


def test_secrets_are_masked_in_repr() -> None:
    settings = Settings(_env_file=None, massive_api_key=SecretStr("super-secret-key"))
    assert "super-secret-key" not in repr(settings)
    assert "super-secret-key" not in str(settings.model_dump())

import re
from pathlib import Path

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


ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
# `make test-api` mounts only api/ into its container; CI runs these from the full checkout.
needs_env_example = pytest.mark.skipif(not ENV_EXAMPLE.is_file(), reason="no .env.example here")


@needs_env_example
def test_env_example_as_copied_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    # `cp .env.example .env` must start: empty values mean "not set" (REPLAY_START= used to fail
    # its HH:MM pattern), and the process environment carries the same keys under Compose.
    for line in ENV_EXAMPLE.read_text().splitlines():
        if re.match(r"^[A-Z0-9_]+=", line):
            monkeypatch.delenv(line.split("=", 1)[0], raising=False)
    settings = Settings(_env_file=ENV_EXAMPLE)
    assert settings.replay_start is None
    assert settings.massive_api_key is None
    assert settings.session_secret.get_secret_value() == DEV_SESSION_SECRET
    monkeypatch.setenv("REPLAY_START", "")
    monkeypatch.setenv("ANTHROPIC_MODEL", "")
    assert Settings(_env_file=None).replay_start is None
    assert Settings(_env_file=None).anthropic_model is None


@needs_env_example
def test_env_example_keeps_notes_off_empty_values() -> None:
    # Docker Compose reads `KEY=   # note` as the value "# note"; python-dotenv reads it as empty.
    bad = [
        line for line in ENV_EXAMPLE.read_text().splitlines() if re.match(r"^[A-Z0-9_]+=\s*#", line)
    ]
    assert bad == []

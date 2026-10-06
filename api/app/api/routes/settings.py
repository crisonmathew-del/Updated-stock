"""Methodology, universe and account settings (spec §8.10, §14), and which data sources and
services have their keys configured (never the keys themselves).

- GET   /settings          every setting with its category, description, value, default, bounds
- PATCH /settings          {changes: {key: value}}: validated together, saved only if all pass
- POST  /settings/reset    {keys?: [...]}: back to the defaults (all when no keys)
- GET   /settings/keys     each key from .env: configured or missing, and what it switches on
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError

from app.api.deps import DbSession, current_user
from app.core.config import Settings, get_settings
from app.settings import store
from app.settings.schema import DEFAULTS, AppSettings, category_of

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(current_user)])

_PROPERTIES: dict[str, Any] = AppSettings.model_json_schema()["properties"]
_CONSTRAINTS = (
    "type",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "pattern",
    "enum",
)


class SettingItem(BaseModel):
    key: str
    category: str
    description: str
    value: Any
    default: Any
    constraints: dict[str, Any]


class SettingsResponse(BaseModel):
    items: list[SettingItem]


class SettingsUpdate(BaseModel):
    changes: dict[str, Any]


class SettingsReset(BaseModel):
    keys: list[str] | None = None


def _response(settings: AppSettings) -> SettingsResponse:
    values = settings.model_dump(mode="json")
    defaults = DEFAULTS.model_dump(mode="json")
    items = []
    for key, field in AppSettings.model_fields.items():
        prop = _PROPERTIES.get(key, {})
        items.append(
            SettingItem(
                key=key,
                category=category_of(key),
                description=field.description or key,
                value=values[key],
                default=defaults[key],
                constraints={k: prop[k] for k in _CONSTRAINTS if k in prop},
            )
        )
    return SettingsResponse(items=items)


def _validation_detail(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"key": ".".join(str(p) for p in err["loc"]) or "settings", "message": err["msg"]}
        for err in exc.errors()
    ]


@router.get("", response_model=SettingsResponse)
async def get_settings_values(db: DbSession) -> SettingsResponse:
    return _response(await store.load(db))


@router.patch("", response_model=SettingsResponse)
async def update_settings(body: SettingsUpdate, db: DbSession) -> SettingsResponse:
    try:
        return _response(await store.update(db, body.changes))
    except store.UnknownSettingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown setting: {exc.args[0]}") from exc
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _validation_detail(exc)) from exc


@router.post("/reset", response_model=SettingsResponse)
async def reset_settings(body: SettingsReset, db: DbSession) -> SettingsResponse:
    try:
        return _response(await store.reset(db, body.keys))
    except store.UnknownSettingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown setting: {exc.args[0]}") from exc


class KeyStatus(BaseModel):
    name: str
    env: list[str]  # the .env variables it needs
    configured: bool
    in_use: bool  # selected by the provider settings (or always used when configured)
    purpose: str


class ProvidersOut(BaseModel):
    providers: dict[str, str]
    keys: list[KeyStatus]


def _set(value: object) -> bool:
    if value is None:
        return False
    secret = getattr(value, "get_secret_value", None)
    text = secret() if callable(secret) else value
    return bool(str(text).strip())


def key_status(c: Settings) -> ProvidersOut:
    smtp = _set(c.smtp_host)
    keys = [
        KeyStatus(
            name="Massive",
            env=["MASSIVE_API_KEY"],
            configured=_set(c.massive_api_key),
            in_use=c.price_provider == "massive",
            purpose="Daily prices for production (PRICE_PROVIDER=massive)",
        ),
        KeyStatus(
            name="Alpaca",
            env=["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"],
            configured=_set(c.alpaca_api_key_id) and _set(c.alpaca_api_secret_key),
            in_use=c.stream_provider == "alpaca",
            purpose="Real-time trades for intraday alerts (STREAM_PROVIDER=alpaca)",
        ),
        KeyStatus(
            name="Financial Modeling Prep",
            env=["FMP_API_KEY"],
            configured=_set(c.fmp_api_key),
            in_use=c.fundamentals_provider == "fmp",
            purpose="Fundamentals (FUNDAMENTALS_PROVIDER=fmp)",
        ),
        KeyStatus(
            name="Finnhub",
            env=["FINNHUB_API_KEY"],
            configured=_set(c.finnhub_api_key),
            in_use=c.news_provider == "finnhub",
            purpose="News headlines (NEWS_PROVIDER=finnhub)",
        ),
        KeyStatus(
            name="SEC EDGAR contact",
            env=["SEC_USER_AGENT"],
            configured=_set(c.sec_user_agent),
            in_use=c.fundamentals_provider == "sec_edgar",
            purpose="Company IDs, industry codes, market caps and SEC fundamentals (free)",
        ),
        KeyStatus(
            name="Anthropic",
            env=["ANTHROPIC_API_KEY"],
            configured=_set(c.anthropic_api_key),
            in_use=_set(c.anthropic_api_key),
            purpose=f"AI summaries on the stock page ({c.anthropic_model or 'claude-opus-5-5'})",
        ),
        KeyStatus(
            name="Resend",
            env=["RESEND_API_KEY"],
            configured=_set(c.resend_api_key),
            in_use=c.email_provider == "resend",
            purpose="Alert emails (EMAIL_PROVIDER=resend)",
        ),
        KeyStatus(
            name="SMTP",
            env=["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"],
            configured=smtp,
            in_use=c.email_provider == "smtp",
            purpose="Alert emails through your own mail server (EMAIL_PROVIDER=smtp)",
        ),
    ]
    return ProvidersOut(
        providers={
            "prices": c.price_provider,
            "stream": c.stream_provider,
            "fundamentals": c.fundamentals_provider,
            "news": c.news_provider,
            "email": c.email_provider,
        },
        keys=keys,
    )


@router.get("/keys")
async def keys() -> ProvidersOut:
    return key_status(get_settings())

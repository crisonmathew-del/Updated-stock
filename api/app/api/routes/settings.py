"""Methodology, universe and account settings (spec §8.10, §14)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError

from app.api.deps import DbSession, current_user
from app.settings import store
from app.settings.schema import DEFAULTS, AppSettings, category_of

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(current_user)])

_PROPERTIES: dict[str, Any] = AppSettings.model_json_schema()["properties"]
_CONSTRAINTS = ("type", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "pattern")


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

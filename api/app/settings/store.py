"""Read and write `AppSettings` against the `settings` table."""

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.settings.schema import DEFAULTS, AppSettings


class UnknownSettingError(KeyError):
    pass


def _dump_defaults() -> dict[str, Any]:
    return DEFAULTS.model_dump(mode="json")


async def load(session: AsyncSession) -> AppSettings:
    """Current settings: stored values over code defaults. Keys no longer defined are ignored."""
    rows = (await session.execute(select(Setting.key, Setting.value))).all()
    values = _dump_defaults()
    values.update({key: value for key, value in rows if key in AppSettings.model_fields})
    return AppSettings.model_validate(values)


async def seed(session: AsyncSession) -> list[str]:
    """Insert every setting that has no stored value yet. Never overwrites changes.
    Returns the keys that were added."""
    defaults = _dump_defaults()
    existing = set((await session.execute(select(Setting.key))).scalars())
    missing = [key for key in defaults if key not in existing]
    if missing:
        await session.execute(
            insert(Setting)
            .values([{"key": key, "value": defaults[key]} for key in missing])
            .on_conflict_do_nothing(index_elements=["key"])
        )
    await session.commit()
    return missing


async def update(session: AsyncSession, changes: dict[str, Any]) -> AppSettings:
    """Validate the full settings with `changes` applied, then persist only the changed keys.
    Raises `UnknownSettingError` or pydantic `ValidationError`; nothing is written on error."""
    unknown = sorted(set(changes) - set(AppSettings.model_fields))
    if unknown:
        raise UnknownSettingError(", ".join(unknown))

    current = await load(session)
    merged = {**current.model_dump(mode="json"), **changes}
    validated = AppSettings.model_validate(merged)
    as_json = validated.model_dump(mode="json")

    for key in changes:
        await session.execute(
            insert(Setting)
            .values(key=key, value=as_json[key])
            .on_conflict_do_update(index_elements=["key"], set_={"value": as_json[key]})
        )
    await session.commit()
    return validated


async def reset(session: AsyncSession, keys: list[str] | None = None) -> AppSettings:
    """Reset the given keys (or everything) to the code defaults."""
    defaults = _dump_defaults()
    targets = list(defaults) if keys is None else keys
    unknown = sorted(set(targets) - set(defaults))
    if unknown:
        raise UnknownSettingError(", ".join(unknown))
    return await update(session, {key: defaults[key] for key in targets})


__all__ = ["UnknownSettingError", "ValidationError", "load", "reset", "seed", "update"]

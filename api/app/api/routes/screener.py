"""The screener (spec §8.4): one compact snapshot of every stock on the latest session, which the
browser filters and sorts (spec §10: client-side under 10k rows), plus saved screens.

The snapshot (app.scanner.screener_rows) is columnar (`fields` + `rows` of values in that
order) and cached in Redis until the data, the setups run or the settings change.
"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession, RedisClient, current_user
from app.models import IndicatorDaily, SavedScreen
from app.scanner.daily import setups_through
from app.scanner.screener_rows import FIELDS, build_snapshot
from app.settings import store

router = APIRouter(tags=["screener"], dependencies=[Depends(current_user)])

CACHE_VERSION = 1
CACHE_TTL_SECONDS = 26 * 3600


@router.get("/screener")
async def screener(db: DbSession, redis: RedisClient) -> Response:
    """{as_of, fields, rows, groups}: every stock (common and ADR) on the latest session."""
    as_of = await db.scalar(select(func.max(IndicatorDaily.date)))
    if as_of is None:
        body = json.dumps({"as_of": None, "fields": list(FIELDS), "rows": [], "groups": 0})
        return Response(body, media_type="application/json")
    settings = await store.load(db)
    digest = hashlib.sha1(settings.model_dump_json().encode()).hexdigest()[:12]
    through = await setups_through(db)
    key = f"screener:v{CACHE_VERSION}:{as_of}:{through}:{digest}"
    cached = await redis.get(key)
    if cached is not None:
        return Response(cached, media_type="application/json")
    body = json.dumps(await build_snapshot(db, as_of), separators=(",", ":"))
    await redis.set(key, body, ex=CACHE_TTL_SECONDS)
    return Response(body, media_type="application/json")


# --- Saved screens --------------------------------------------------------------------------

Op = Literal["between", "is", "in"]


class FilterIn(BaseModel):
    field: str = Field(min_length=1, max_length=40)
    op: Op
    min: float | None = None
    max: float | None = None
    value: bool | None = None
    values: list[str] | None = None


class SortIn(BaseModel):
    field: str
    desc: bool = True


class ScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: list[FilterIn] = Field(default_factory=list, max_length=40)
    sort: SortIn | None = None
    columns: list[str] | None = None


class ScreenPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    filters: list[FilterIn] | None = None
    sort: SortIn | None = None
    columns: list[str] | None = None


class ScreenOut(BaseModel):
    id: int
    name: str
    filters: list[FilterIn]
    sort: SortIn | None
    columns: list[str] | None
    updated_at: datetime


def _check_fields(filters: list[FilterIn] | None, columns: list[str] | None) -> None:
    unknown = sorted(
        {f.field for f in filters or [] if f.field not in FIELDS}
        | {c for c in columns or [] if c not in FIELDS}
    )
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown screener field(s): {', '.join(unknown)}.",
        )


def _screen_out(screen: SavedScreen) -> ScreenOut:
    return ScreenOut(
        id=screen.id,
        name=screen.name,
        filters=[FilterIn(**f) for f in screen.filters],
        sort=SortIn(**screen.sort) if screen.sort else None,
        columns=screen.columns,
        updated_at=screen.updated_at,
    )


async def _own_screen(db: DbSession, user: AuthUser, screen_id: int) -> SavedScreen:
    screen = await db.get(SavedScreen, screen_id)
    if screen is None or screen.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No saved screen {screen_id}.")
    return screen


async def _name_taken(db: DbSession, user: AuthUser, name: str, other: int | None = None) -> None:
    query = select(SavedScreen.id).where(SavedScreen.user_id == user.id, SavedScreen.name == name)
    found = await db.scalar(query)
    if found is not None and found != other:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'A screen called "{name}" already exists. Pick another name or update that one.',
        )


@router.get("/screens", response_model=list[ScreenOut])
async def list_screens(db: DbSession, user: AuthUser) -> list[ScreenOut]:
    rows = await db.scalars(
        select(SavedScreen).where(SavedScreen.user_id == user.id).order_by(SavedScreen.name)
    )
    return [_screen_out(s) for s in rows.all()]


@router.post("/screens", response_model=ScreenOut, status_code=status.HTTP_201_CREATED)
async def create_screen(db: DbSession, user: AuthUser, payload: ScreenIn) -> ScreenOut:
    _check_fields(payload.filters, payload.columns)
    await _name_taken(db, user, payload.name)
    screen = SavedScreen(
        user_id=user.id,
        name=payload.name,
        filters=[f.model_dump(exclude_none=True) for f in payload.filters],
        sort=payload.sort.model_dump() if payload.sort else None,
        columns=payload.columns,
    )
    db.add(screen)
    await db.commit()
    await db.refresh(screen)
    return _screen_out(screen)


@router.patch("/screens/{screen_id}", response_model=ScreenOut)
async def update_screen(
    db: DbSession, user: AuthUser, screen_id: int, payload: ScreenPatch
) -> ScreenOut:
    screen = await _own_screen(db, user, screen_id)
    _check_fields(payload.filters, payload.columns)
    if payload.name is not None:
        await _name_taken(db, user, payload.name, other=screen.id)
        screen.name = payload.name
    if payload.filters is not None:
        screen.filters = [f.model_dump(exclude_none=True) for f in payload.filters]
    if "sort" in payload.model_fields_set:
        screen.sort = payload.sort.model_dump() if payload.sort else None
    if payload.columns is not None:
        screen.columns = payload.columns
    await db.commit()
    await db.refresh(screen)
    return _screen_out(screen)


@router.delete("/screens/{screen_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_screen(db: DbSession, user: AuthUser, screen_id: int) -> Response:
    screen = await _own_screen(db, user, screen_id)
    await db.delete(screen)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

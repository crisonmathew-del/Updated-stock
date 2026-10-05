"""The screener (spec §8.4): one compact snapshot of every stock on the latest session, which the
browser filters and sorts (spec §10: client-side under 10k rows), plus saved screens.

The snapshot is columnar (`fields` + `rows` of values in that order) and cached in Redis until
the data, the setups run or the settings change. `spark` is the last 120 closes sampled to 40
points, scaled 0-255 and base64-encoded (one byte per point) to keep 6,000 rows small.
"""

import base64
import hashlib
import json
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession, RedisClient, current_user
from app.core.calendar import sessions_back
from app.data.loaders import read_frame
from app.models import IndicatorDaily, SavedScreen
from app.scanner.daily import setups_through
from app.scanner.snapshot import indicators_on
from app.scanner.universe_filter import liquid_tickers
from app.settings import store

router = APIRouter(tags=["screener"], dependencies=[Depends(current_user)])

CACHE_VERSION = 1
CACHE_TTL_SECONDS = 26 * 3600
SPARK_SESSIONS = 120
SPARK_POINTS = 40
GAP_RECENT_SESSIONS = 5

FIELDS = (
    "symbol",
    "name",
    "type",
    "sector",
    "group",
    "group_rank",
    "close",
    "change_pct",
    "volume",
    "volume_ratio",
    "dollar_volume",
    "market_cap",
    "rs_rating",
    "rs_line_high",
    "rs_ahead",
    "stage",
    "tt_passed",
    "tt_pass",
    "off_high_pct",
    "above_low_pct",
    "vs_sma50_pct",
    "fund_grade",
    "setup_state",
    "setup_kind",
    "pattern",
    "grade",
    "score",
    "readiness_pct",
    "pivot",
    "breakout_today",
    "pocket_pivot_today",
    "earnings_gap_recent",
    "liquid",
    "spark",
)


def spark(closes: list[float]) -> str:
    """Closes → SPARK_POINTS evenly sampled points scaled 0-255, base64 (empty when flat or
    too short)."""
    values = np.array([c for c in closes if c is not None], dtype=float)
    if len(values) < 2:
        return ""
    idx = np.linspace(0, len(values) - 1, min(SPARK_POINTS, len(values))).round().astype(int)
    sampled = values[idx]
    low, high = sampled.min(), sampled.max()
    if high <= low:
        return base64.b64encode(bytes([128] * len(sampled))).decode()
    scaled = np.round((sampled - low) / (high - low) * 255).astype(np.uint8)
    return base64.b64encode(scaled.tobytes()).decode()


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100, 2)


async def _build(db: DbSession, as_of: date) -> dict[str, Any]:
    settings = await store.load(db)
    tickers = await read_frame(
        "SELECT t.id AS ticker_id, t.symbol, t.name, t.type, t.sector, t.market_cap, "
        "g.name AS group_name, r.rank AS group_rank FROM tickers t "
        "LEFT JOIN industry_groups g ON g.id = t.industry_group_id "
        "LEFT JOIN group_rank_history r ON r.group_id = t.industry_group_id "
        f"AND r.date = '{as_of.isoformat()}' "
        "WHERE t.active AND NOT t.is_benchmark AND t.type IN ('common', 'adr')"
    )
    snap = await indicators_on(as_of, settings)
    if tickers.is_empty() or snap.is_empty():
        return {"as_of": as_of.isoformat(), "fields": list(FIELDS), "rows": [], "groups": 0}
    frame = tickers.join(snap, on="ticker_id", how="inner")
    grades = await read_frame(
        "SELECT DISTINCT ON (ticker_id) ticker_id, grade AS fund_grade FROM fundamental_grades "
        f"WHERE date <= '{as_of.isoformat()}' ORDER BY ticker_id, date DESC"
    )
    setups = await read_frame(
        "SELECT ticker_id, state AS setup_state, kind AS setup_kind, pattern_type, grade, "
        "score, readiness_pct, pivot FROM setups WHERE active"
    )
    for extra in (grades, setups):
        if not extra.is_empty():
            frame = frame.join(extra, on="ticker_id", how="left")
    breakouts = await read_frame(
        f"SELECT DISTINCT ticker_id FROM setups WHERE breakout_date = '{as_of.isoformat()}'"
    )
    pivots = await read_frame(
        "SELECT DISTINCT ticker_id FROM patterns WHERE type = 'pocket_pivot' "
        f"AND start_date = '{as_of.isoformat()}'"
    )
    gap_since = sessions_back(as_of, GAP_RECENT_SESSIONS - 1)
    gaps = await read_frame(
        "SELECT DISTINCT ticker_id FROM patterns WHERE type = 'earnings_gap' "
        f"AND start_date BETWEEN '{gap_since.isoformat()}' AND '{as_of.isoformat()}' "
        "AND status = 'forming'"
    )
    flags = {
        "breakout_today": set(breakouts["ticker_id"].to_list()) if breakouts.height else set(),
        "pocket_pivot_today": set(pivots["ticker_id"].to_list()) if pivots.height else set(),
        "earnings_gap_recent": set(gaps["ticker_id"].to_list()) if gaps.height else set(),
    }
    liquid = await liquid_tickers(db, settings, as_of)
    spark_start = sessions_back(as_of, SPARK_SESSIONS - 1)
    closes = await read_frame(
        "SELECT ticker_id, date, close FROM daily_bars "
        f"WHERE date BETWEEN '{spark_start.isoformat()}' AND '{as_of.isoformat()}' "
        "ORDER BY ticker_id, date"
    )
    sparks: dict[int, str] = {}
    if closes.height:
        for (tid,), part in closes.partition_by("ticker_id", as_dict=True).items():
            sparks[int(tid)] = spark(part["close"].to_list())
    for column, dtype in (
        ("fund_grade", pl.String),
        ("setup_state", pl.String),
        ("setup_kind", pl.String),
        ("pattern_type", pl.String),
        ("grade", pl.String),
        ("score", pl.Float64),
        ("readiness_pct", pl.Float64),
        ("pivot", pl.Float64),
    ):
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(column))

    rows = []
    for r in frame.sort("symbol").iter_rows(named=True):
        tid = int(r["ticker_id"])
        close = r["close"]
        rows.append(
            [
                r["symbol"],
                r["name"],
                r["type"],
                r["sector"],
                r["group_name"],
                r["group_rank"],
                close,
                _pct(close, r["prev_close"]),
                r["volume"],
                None if r["volume_ratio"] is None else round(r["volume_ratio"], 2),
                r["avg_dollar_volume_50"],
                r["market_cap"],
                r["rs_rating"],
                bool(r["rs_line_high_52w"]),
                bool(r["rs_new_high_ahead"]),
                r["stage"],
                r["tt_passed"],
                bool(r["tt_pass"]),
                _pct(close, r["high_52w"]),
                _pct(close, r["low_52w"]),
                _pct(close, r["sma50"]),
                r["fund_grade"],
                r["setup_state"],
                r["setup_kind"],
                r["pattern_type"],
                r["grade"],
                None if r["score"] is None else round(r["score"], 1),
                r["readiness_pct"],
                r["pivot"],
                tid in flags["breakout_today"],
                tid in flags["pocket_pivot_today"],
                tid in flags["earnings_gap_recent"],
                tid in liquid,
                sparks.get(tid, ""),
            ]
        )
    groups = await read_frame(
        f"SELECT count(*) AS n FROM group_rank_history WHERE date = '{as_of.isoformat()}'"
    )
    return {
        "as_of": as_of.isoformat(),
        "fields": list(FIELDS),
        "rows": rows,
        "groups": int(groups["n"][0]) if groups.height else 0,
    }


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
    body = json.dumps(await _build(db, as_of), separators=(",", ":"))
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

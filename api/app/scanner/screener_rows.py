"""The screener snapshot: one row per stock (common and ADR) on a session with everything the
screener filters and shows. Built by the screener API (cached) and the EOD screen alerts.

`spark` is the last 120 closes sampled to 40 points, scaled 0-255 and base64-encoded (one byte
per point) to keep 6,000 rows small.
"""

import base64
from datetime import date
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_back
from app.data.loaders import read_frame
from app.scanner.snapshot import indicators_on
from app.scanner.universe_filter import liquid_tickers
from app.settings import store

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


async def build_snapshot(db: AsyncSession, as_of: date) -> dict[str, Any]:
    """{as_of, fields, rows, groups} for the session `as_of`."""
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


def row_dicts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    fields = snapshot["fields"]
    return [dict(zip(fields, row, strict=True)) for row in snapshot["rows"]]


def matches(row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
    """A saved screen's filters on one row: the same rules as the web's `matches`
    (web/lib/screener.ts): yes/no, one of, and between (an open end is no limit)."""
    for f in filters:
        value = row.get(f.get("field", ""))
        op = f.get("op")
        if op == "is":
            if bool(value) != bool(f.get("value")):
                return False
        elif op == "in":
            values = f.get("values") or []
            if values and value not in values:
                return False
        else:
            low, high = f.get("min"), f.get("max")
            if low is None and high is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int | float):
                return False
            if low is not None and value < low:
                return False
            if high is not None and value > high:
                return False
    return True

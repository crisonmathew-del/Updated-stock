"""Run the detectors for many stocks as of a session and keep the `patterns` table current.

Storage rules (see app.models.patterns):
- A detection upserts its row (ticker, type, timeframe, start date). A run for an older date
  never overwrites a row last seen on a later date.
- A base that was forming but isn't detected any more becomes `failed` if the close is below
  its base low, else `expired` (it no longer meets the rules), as of the run's date.
- Events (pocket pivots, earnings gaps) are kept as detected; they don't expire.
"""

import asyncio
import multiprocessing
import os
import time
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import ends_week, sessions_back
from app.core.config import get_settings
from app.core.logging import get_logger
from app.data.loaders import read_frame
from app.models import Pattern
from app.patterns.bars import INDICATORS, Bars
from app.patterns.detect import detect_patterns
from app.patterns.types import EVENTS, PatternMatch
from app.settings.schema import AppSettings

log = get_logger(__name__)

# Sessions of history each detection sees: the longest base (65 weeks) plus the prior-uptrend
# lookback and the left-side check, with room to spare.
PATTERN_SESSIONS = 600
CHUNK = 250
PARALLEL_MIN_STOCKS = 300  # below this, detection runs in the scan's own process


@dataclass
class PatternRun:
    as_of: date
    scanned: int = 0
    detected: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    expired: int = 0
    seconds: float = 0.0
    matches: list[tuple[int, PatternMatch]] = field(default_factory=list)
    closes: dict[int, float] = field(default_factory=dict)  # every scanned stock with a bar

    def stats(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "scanned": self.scanned,
            "detected": self.detected,
            "by_type": self.by_type,
            "failed": self.failed,
            "expired": self.expired,
            "seconds": round(self.seconds, 1),
        }


async def _correction_dates(as_of: date) -> set[date]:
    frame = await read_frame(
        "SELECT date FROM market_regime_daily WHERE index_symbol = 'MARKET' "
        f"AND state = 'correction' AND date <= '{as_of.isoformat()}'"
    )
    return set(frame["date"].to_list()) if not frame.is_empty() else set()


async def _releases(ids: str, as_of: date) -> dict[int, list[tuple[date, str]]]:
    frame = await read_frame(
        "SELECT ticker_id, report_date, timing FROM earnings_calendar "
        f"WHERE ticker_id IN ({ids}) AND status = 'reported' "
        f"AND report_date <= '{as_of.isoformat()}' ORDER BY report_date"
    )
    out: dict[int, list[tuple[date, str]]] = defaultdict(list)
    for tid, day, timing in frame.iter_rows():
        out[int(tid)].append((day, str(timing)))
    return out


def _detect_frame(
    frame: pl.DataFrame,
    as_of: date,
    settings: AppSettings,
    corrections: set[date],
    week_complete: bool,
    releases: dict[int, list[tuple[date, str]]],
) -> tuple[list[tuple[int, PatternMatch]], dict[int, float]]:
    """Detections for every stock in `frame` (bars + indicators through `as_of`). Runs in a
    worker process, so it takes and returns only picklable values."""
    found: list[tuple[int, PatternMatch]] = []
    closes: dict[int, float] = {}
    for (tid,), rows in frame.partition_by("ticker_id", as_dict=True).items():
        bars = Bars.from_frame(rows, corrections)
        if not len(bars) or bars.dates[-1] != as_of:
            continue
        ticker = int(tid)
        closes[ticker] = float(bars.close[-1])
        for match in detect_patterns(
            bars, settings, last_week_complete=week_complete, releases=releases.get(ticker, [])
        ):
            found.append((ticker, match))
    return found, closes


def pattern_workers(stocks: int) -> int:
    """Processes to use for `stocks` stocks: none for a handful (start-up costs more)."""
    if stocks < PARALLEL_MIN_STOCKS:
        return 1
    configured = get_settings().pattern_workers
    return configured or max(1, (os.cpu_count() or 2) - 1)


def _pool(workers: int) -> ProcessPoolExecutor:
    # forkserver, not fork: the scan runs inside an asyncio process with connectorx threads,
    # and forking a threaded process can deadlock. The server preloads the heavy imports once.
    context = multiprocessing.get_context("forkserver")
    context.set_forkserver_preload(["app.patterns.detect", "polars", "numpy"])
    return ProcessPoolExecutor(max_workers=workers, mp_context=context)


async def detect_stocks(
    settings: AppSettings,
    as_of: date,
    ticker_ids: Sequence[int],
    *,
    workers: int | None = None,
    chunk: int = CHUNK,
) -> tuple[list[tuple[int, PatternMatch]], dict[int, float]]:
    """Detections as of `as_of` for stocks with a bar that day, plus their closes. Chunks are
    read here and detected in `workers` processes (default: pattern_workers) while the next
    chunk loads; results are sorted, so they don't depend on which process finished first."""
    corrections = await _correction_dates(as_of)
    week_complete = ends_week(as_of)
    start = sessions_back(as_of, PATTERN_SESSIONS)
    columns = ", ".join(f"i.{c}" for c in INDICATORS)
    count = workers if workers is not None else pattern_workers(len(ticker_ids))
    found: list[tuple[int, PatternMatch]] = []
    closes: dict[int, float] = {}

    def collect(result: tuple[list[tuple[int, PatternMatch]], dict[int, float]]) -> None:
        found.extend(result[0])
        closes.update(result[1])

    pool = _pool(count) if count > 1 else None
    loop = asyncio.get_running_loop()
    pending: set[asyncio.Future[tuple[list[tuple[int, PatternMatch]], dict[int, float]]]] = set()
    try:
        for first in range(0, len(ticker_ids), chunk):
            ids = ",".join(str(int(t)) for t in ticker_ids[first : first + chunk])
            frame = await read_frame(
                "SELECT b.ticker_id, b.date, b.open, b.high, b.low, b.close, b.volume, "
                f"{columns} FROM daily_bars b JOIN indicators_daily i "
                "ON i.ticker_id = b.ticker_id AND i.date = b.date "
                f"WHERE b.ticker_id IN ({ids}) AND b.date BETWEEN '{start.isoformat()}' "
                f"AND '{as_of.isoformat()}' ORDER BY b.ticker_id, b.date"
            )
            if frame.is_empty():
                continue
            releases = await _releases(ids, as_of)
            args = (frame, as_of, settings, corrections, week_complete, releases)
            if pool is None:
                collect(_detect_frame(*args))
                continue
            pending.add(loop.run_in_executor(pool, _detect_frame, *args))
            if len(pending) >= 2 * count:  # bound the frames held in memory
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    collect(future.result())
        for future in asyncio.as_completed(pending):
            collect(await future)
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    found.sort(key=lambda tm: (tm[0], str(tm[1].type), tm[1].timeframe, tm[1].start))
    return found, closes


async def store_patterns(
    session: AsyncSession,
    matches: Sequence[tuple[int, PatternMatch]],
    as_of: date,
    closes: dict[int, float],
) -> tuple[int, int]:
    """Upsert detections and retire bases that stopped matching. Returns (failed, expired)."""
    rows = [
        {
            **m.as_row(),
            "ticker_id": tid,
            "status_date": as_of,
            "first_detected": as_of,
            "last_seen": as_of,
        }
        for tid, m in matches
    ]
    for start in range(0, len(rows), 500):
        stmt = insert(Pattern).values(rows[start : start + 500])
        new = stmt.excluded
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["ticker_id", "type", "timeframe", "start_date"],
                set_={
                    "end_date": new.end_date,
                    "pivot": new.pivot,
                    "base_low": new.base_low,
                    "depth_pct": new.depth_pct,
                    "duration_weeks": new.duration_weeks,
                    "quality": new.quality,
                    "base_number": new.base_number,
                    "status": new.status,
                    "status_date": text(
                        "CASE WHEN patterns.status = excluded.status "
                        "THEN patterns.status_date ELSE excluded.status_date END"
                    ),
                    "components": new.components,
                    "swings": new.swings,
                    "contractions": new.contractions,
                    "details": new.details,
                    "first_detected": text(
                        "least(patterns.first_detected, excluded.first_detected)"
                    ),
                    "last_seen": new.last_seen,
                    "updated_at": text("now()"),
                },
                where=Pattern.last_seen <= new.last_seen,
            )
        )
    failed = expired = 0
    if closes:
        values = ", ".join(f"({int(t)}, {float(c)!r})" for t, c in closes.items())
        events = ", ".join(f"'{e}'" for e in sorted(EVENTS))
        result = await session.execute(
            text(
                "UPDATE patterns p SET "
                "status = CASE WHEN c.close < p.base_low THEN 'failed' ELSE 'expired' END, "
                "status_date = :as_of, updated_at = now() "
                f"FROM (VALUES {values}) AS c(ticker_id, close) "
                "WHERE p.ticker_id = c.ticker_id AND p.status = 'forming' "
                f"AND p.last_seen < :as_of AND p.type NOT IN ({events}) "
                "RETURNING p.status"
            ),
            {"as_of": as_of},
        )
        statuses = [s for (s,) in result.all()]
        failed, expired = statuses.count("failed"), statuses.count("expired")
    await session.commit()
    return failed, expired


async def run_patterns(
    session: AsyncSession, settings: AppSettings, as_of: date, ticker_ids: Sequence[int]
) -> PatternRun:
    started = time.perf_counter()
    run = PatternRun(as_of)
    matches, closes = await detect_stocks(settings, as_of, ticker_ids)
    run.scanned = len(closes)
    run.detected = len(matches)
    for _, m in matches:
        run.by_type[str(m.type)] = run.by_type.get(str(m.type), 0) + 1
    run.failed, run.expired = await store_patterns(session, matches, as_of, closes)
    run.matches = list(matches)
    run.closes = closes
    run.seconds = time.perf_counter() - started
    log.info("patterns.done", **run.stats())
    return run

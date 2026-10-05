"""End-of-day analytics pipeline (spec §7.1 EOD scan, Phases 2-3):

    bars → indicators + RS Rating → industry groups → breadth → group ranks → market regime
         → Fundamentals Grade → pattern detection (as of the latest session)

Three ways to bring `indicators_daily` up to date:
- Full rebuild (first run, or many tickers changed): every ticker's whole history. RS raw is
  computed for all stocks first so RS Ratings can be ranked per date, then indicators are
  computed and written chunk by chunk.
- Stale recompute: tickers whose bars were rewritten (new listing, split re-fetch) get their
  whole history recomputed; their RS Ratings are ranked against the stored values of everyone
  else on each date.
- Incremental: only sessions after the last computed date, using a `LOOKBACK_SESSIONS` window
  of bars so recursive averages (EMA, Wilder ATR) have fully converged.

Everything for date D uses bars dated on or before D only.
"""

import asyncio
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_back
from app.core.logging import get_logger
from app.data.loaders import append_frame, load_bars, read_frame
from app.groups.classification import TickerSic, assign_groups, sector_for
from app.groups.industry_rank import add_rank_trend, group_member_rows, rank_groups
from app.indicators.compute import INDICATOR_COLUMNS, compute_indicators
from app.indicators.frame import DATE, TICKER, per_ticker
from app.indicators.relative_strength import add_rs_raw, rs_ratings
from app.market.breadth import breadth_counts, finalize_breadth
from app.market.regime import LABELS, MARKET, RegimeDay, combine_market, compute_index_regime
from app.models import (
    GroupRankDaily,
    IndicatorDaily,
    IndustryGroup,
    MarketBreadthDaily,
    MarketRegimeDaily,
    Ticker,
    TickerType,
)
from app.scanner.detection import run_detection
from app.scoring.trend_template import add_sma200_ago, evaluate_trend_template
from app.settings.schema import AppSettings

log = get_logger(__name__)

LOOKBACK_SESSIONS = 600
CHUNK_TICKERS = 250
FULL_REBUILD_STALE_SHARE = 0.2
HISTORY_START = date(1990, 1, 1)
BENCHMARK = "SPY"
REGIME_INDEXES = ("SPY", "QQQ", "IWM")

STORED_COLUMNS: tuple[str, ...] = (TICKER, DATE, *INDICATOR_COLUMNS, "rs_rating")

# Bulk loading 12M+ rows is ~6x faster without per-row index and foreign-key maintenance, so a
# full rebuild drops these and rebuilds them once at the end. `_ensure_indicator_constraints`
# runs at the start of every pass, so an interrupted rebuild can't leave them missing.
INDICATOR_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (
        "pk_indicators_daily",
        "ALTER TABLE indicators_daily ADD CONSTRAINT pk_indicators_daily "
        "PRIMARY KEY (ticker_id, date)",
    ),
    (
        "fk_indicators_daily_ticker_id_tickers",
        "ALTER TABLE indicators_daily ADD CONSTRAINT fk_indicators_daily_ticker_id_tickers "
        "FOREIGN KEY (ticker_id) REFERENCES tickers (id) ON DELETE CASCADE",
    ),
)
DATE_INDEX = "CREATE INDEX IF NOT EXISTS indicators_daily_date_idx ON indicators_daily (date DESC)"
STORED_SCHEMA: dict[str, pl.DataType] = {
    TICKER: pl.Int32(),
    DATE: pl.Date(),
    "history_sessions": pl.Int32(),
    "rs_rating": pl.Int16(),
    "stage": pl.Int16(),
    "rs_line_high_52w": pl.Boolean(),
    "rs_new_high_ahead": pl.Boolean(),
}


@dataclass(frozen=True)
class TickerInfo:
    id: int
    symbol: str
    type: str
    is_benchmark: bool
    stale: bool
    sic_code: str | None
    sic_description: str | None

    @property
    def eligible(self) -> bool:
        """Ranked for RS Rating, breadth and groups: stocks, not funds or indexes."""
        return self.type in (TickerType.COMMON, TickerType.ADR) and not self.is_benchmark


@dataclass
class AnalyticsResult:
    mode: str = "none"
    tickers: int = 0
    recomputed: list[str] = field(default_factory=list)
    new_dates: list[date] = field(default_factory=list)
    rows_written: int = 0
    groups: int = 0
    market_state: str | None = None
    detection: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tickers": self.tickers,
            "recomputed": len(self.recomputed),
            "new_dates": [d.isoformat() for d in self.new_dates],
            "rows_written": self.rows_written,
            "groups": self.groups,
            "market_state": self.market_state,
            **self.detection,
            "seconds": round(self.seconds, 1),
        }


def _date_bound(series: pl.Series, *, latest: bool = False) -> date:
    value = series.max() if latest else series.min()
    if not isinstance(value, date):
        raise ValueError(f"expected a date in {series.name}, got {value!r}")
    return value


def _chunks(items: Sequence[int], size: int = CHUNK_TICKERS) -> Iterator[list[int]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _for_storage(frame: pl.DataFrame) -> pl.DataFrame:
    casts = {name: dtype for name, dtype in STORED_SCHEMA.items() if name in frame.columns}
    return frame.select(STORED_COLUMNS).cast(casts)  # type: ignore[arg-type]


async def _ensure_indicator_constraints(session: AsyncSession) -> None:
    existing = set(
        (
            await session.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'indicators_daily'::regclass"
                )
            )
        ).scalars()
    )
    for name, ddl in INDICATOR_CONSTRAINTS:
        if name not in existing:
            log.info("analytics.restore_constraint", constraint=name)
            await session.execute(text(ddl))
    await session.execute(text(DATE_INDEX))
    await session.commit()


async def _drop_indicator_constraints(session: AsyncSession) -> None:
    for name, _ in reversed(INDICATOR_CONSTRAINTS):
        await session.execute(
            text(f"ALTER TABLE indicators_daily DROP CONSTRAINT IF EXISTS {name}")
        )
    await session.execute(text("DROP INDEX IF EXISTS indicators_daily_date_idx"))
    await session.commit()


async def _tickers(session: AsyncSession) -> list[TickerInfo]:
    rows = await session.execute(
        select(
            Ticker.id,
            Ticker.symbol,
            Ticker.type,
            Ticker.is_benchmark,
            Ticker.indicators_stale,
            Ticker.sic_code,
            Ticker.sic_description,
        ).where(Ticker.bars_start.is_not(None))
    )
    return [TickerInfo(*row) for row in rows.all()]


async def _benchmark(session: AsyncSession, through: date) -> pl.DataFrame | None:
    spy = await session.scalar(
        select(Ticker.id).where(Ticker.symbol == BENCHMARK, Ticker.is_benchmark)
    )
    if spy is None:
        return None
    return (await load_bars(HISTORY_START, through, [spy])).select(DATE, "close")


def _prepare(
    bars: pl.DataFrame, benchmark: pl.DataFrame | None, settings: AppSettings
) -> pl.DataFrame:
    ind = compute_indicators(
        bars,
        benchmark,
        stage_lookback=settings.stage_slope_lookback_days,
        stage_flat_pct=settings.stage_flat_slope_pct,
    )
    ind = add_sma200_ago(ind, settings.ma200_uptrend_lookback_days)
    return ind.with_columns(per_ticker(pl.col("close").shift(1)).alias("prev_close"))


# --- Groups ---------------------------------------------------------------------------------


async def sync_groups(
    session: AsyncSession, tickers: Sequence[TickerInfo], settings: AppSettings
) -> dict[int, int]:
    """Assign industry groups from SIC codes, store them, and return {ticker_id: group_id}."""
    stocks = [t for t in tickers if t.eligible]
    definitions, membership = assign_groups(
        [TickerSic(t.id, t.sic_code, t.sic_description) for t in stocks],
        settings.group_min_members,
    )
    if definitions:
        stmt = insert(IndustryGroup).values(
            [
                {"code": g.code, "name": g.name, "sector": g.sector, "sic_level": g.sic_level}
                for g in definitions.values()
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["code"],
                set_={
                    "name": stmt.excluded.name,
                    "sector": stmt.excluded.sector,
                    "sic_level": stmt.excluded.sic_level,
                },
            )
        )
    ids = dict((await session.execute(select(IndustryGroup.code, IndustryGroup.id))).all())
    group_ids = {tid: ids[code] for tid, code in membership.items()}
    for t in stocks:
        code = membership.get(t.id)
        await session.execute(
            update(Ticker)
            .where(Ticker.id == t.id)
            .values(
                industry_group_id=group_ids.get(t.id),
                industry=definitions[code].name if code else None,
                sector=sector_for(t.sic_code) if t.sic_code else None,
            )
        )
    await session.commit()
    return group_ids


# --- Indicator passes -------------------------------------------------------------------------


@dataclass
class _Collected:
    breadth: list[pl.DataFrame] = field(default_factory=list)
    members: list[pl.DataFrame] = field(default_factory=list)

    def add(
        self,
        rated: pl.DataFrame,
        eligible: set[int],
        group_ids: dict[int, int],
        settings: AppSettings,
    ) -> None:
        stocks = rated.filter(pl.col(TICKER).is_in(list(eligible)))
        if stocks.is_empty():
            return
        stocks = evaluate_trend_template(stocks, settings)
        self.breadth.append(breadth_counts(stocks))
        self.members.append(group_member_rows(stocks, group_ids))


async def _full_rebuild(
    session: AsyncSession,
    tickers: Sequence[TickerInfo],
    through: date,
    settings: AppSettings,
    group_ids: dict[int, int],
    result: AnalyticsResult,
) -> _Collected:
    eligible = {t.id for t in tickers if t.eligible}
    raw_parts = []
    for chunk in _chunks(sorted(eligible)):
        bars = await load_bars(HISTORY_START, through, chunk)
        raw_parts.append(add_rs_raw(bars).select(TICKER, DATE, "rs_raw"))
    ratings = rs_ratings(pl.concat(raw_parts)) if raw_parts else None

    benchmark = await _benchmark(session, through)
    await session.execute(text("TRUNCATE indicators_daily"))
    await session.commit()
    await _drop_indicator_constraints(session)
    collected = _Collected()
    ids = sorted(t.id for t in tickers)
    writing: asyncio.Task[None] | None = None
    try:
        for chunk in _chunks(ids):
            bars = await load_bars(HISTORY_START, through, chunk)
            ind = _prepare(bars, benchmark, settings)
            rated = (
                ind.join(ratings, on=[TICKER, DATE], how="left")
                if ratings is not None
                else ind.with_columns(pl.lit(None, pl.Int16).alias("rs_rating"))
            )
            collected.add(rated, eligible, group_ids, settings)
            # Write this chunk in the background while the next one is computed.
            if writing is not None:
                await writing
            writing = asyncio.create_task(append_frame("indicators_daily", _for_storage(rated)))
            result.rows_written += rated.height
        if writing is not None:
            await writing
    finally:
        if writing is not None and not writing.done():
            writing.cancel()
        await session.rollback()
        await _ensure_indicator_constraints(session)
    await _mark_fresh(session, ids)
    return collected


async def _recompute(
    session: AsyncSession,
    stale: Sequence[TickerInfo],
    tickers: Sequence[TickerInfo],
    through: date,
    settings: AppSettings,
    result: AnalyticsResult,
) -> None:
    """Whole-history recompute for a few tickers, ranked against everyone else's stored RS."""
    benchmark = await _benchmark(session, through)
    eligible_others = sorted(t.id for t in tickers if t.eligible and not t.stale)
    for chunk_infos in [stale[i : i + CHUNK_TICKERS] for i in range(0, len(stale), CHUNK_TICKERS)]:
        chunk = [t.id for t in chunk_infos]
        bars = await load_bars(HISTORY_START, through, chunk)
        ind = _prepare(bars, benchmark, settings)
        own = ind.filter(pl.col(TICKER).is_in([t.id for t in chunk_infos if t.eligible]))
        own_raw = own.select(TICKER, DATE, "rs_raw")
        if not own_raw.is_empty() and eligible_others:
            first = _date_bound(own_raw[DATE])
            last = _date_bound(own_raw[DATE], latest=True)
            others = await read_frame(
                "SELECT ticker_id, date, rs_raw FROM indicators_daily "
                f"WHERE date BETWEEN '{first}' AND '{last}' "
                f"AND ticker_id IN ({','.join(map(str, eligible_others))})"
            )
            others = others.cast({TICKER: pl.Int32, "rs_raw": pl.Float64})
            pool = pl.concat([others, own_raw.cast({TICKER: pl.Int32})])
        else:
            pool = own_raw
        ratings = rs_ratings(pool).filter(pl.col(TICKER).is_in(chunk))
        rated = ind.join(ratings, on=[TICKER, DATE], how="left")
        await session.execute(delete(IndicatorDaily).where(IndicatorDaily.ticker_id.in_(chunk)))
        await session.commit()
        await append_frame("indicators_daily", _for_storage(rated))
        result.rows_written += rated.height
        result.recomputed.extend(t.symbol for t in chunk_infos)
        await _mark_fresh(session, chunk)


async def _incremental(
    session: AsyncSession,
    tickers: Sequence[TickerInfo],
    new_dates: list[date],
    settings: AppSettings,
    group_ids: dict[int, int],
    result: AnalyticsResult,
) -> _Collected:
    through, first_new = new_dates[-1], new_dates[0]
    window_start = sessions_back(first_new, LOOKBACK_SESSIONS)
    benchmark = await _benchmark(session, through)
    eligible = {t.id for t in tickers if t.eligible}
    parts = []
    for chunk in _chunks(sorted(t.id for t in tickers)):
        bars = await load_bars(window_start, through, chunk)
        ind = _prepare(bars, benchmark, settings)
        parts.append(ind.filter(pl.col(DATE) >= first_new))
    new = pl.concat(parts) if parts else pl.DataFrame()
    collected = _Collected()
    if new.is_empty():
        return collected
    ratings = rs_ratings(
        new.filter(pl.col(TICKER).is_in(list(eligible))).select(TICKER, DATE, "rs_raw")
    )
    rated = new.join(ratings, on=[TICKER, DATE], how="left")

    await session.execute(text("TRUNCATE staging_indicators_daily"))
    await session.commit()
    await append_frame("staging_indicators_daily", _for_storage(rated))
    columns = ", ".join(STORED_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in STORED_COLUMNS[2:])
    await session.execute(
        text(
            f"INSERT INTO indicators_daily ({columns}) SELECT {columns} "
            f"FROM staging_indicators_daily ON CONFLICT (ticker_id, date) DO UPDATE SET {updates}"
        )
    )
    await session.commit()
    result.rows_written += rated.height
    collected.add(rated, eligible, group_ids, settings)
    return collected


async def _mark_fresh(session: AsyncSession, ticker_ids: Sequence[int]) -> None:
    await session.execute(
        update(Ticker).where(Ticker.id.in_(ticker_ids)).values(indicators_stale=False)
    )
    await session.commit()


# --- Breadth and group ranks -------------------------------------------------------------------


async def _store_breadth(session: AsyncSession, collected: _Collected, *, replace: bool) -> None:
    if not collected.breadth:
        return
    counts = pl.concat(collected.breadth)
    first = _date_bound(counts[DATE])
    start = 0.0
    if not replace:
        previous = await session.scalar(
            select(MarketBreadthDaily.ad_line)
            .where(MarketBreadthDaily.date < first)
            .order_by(MarketBreadthDaily.date.desc())
            .limit(1)
        )
        start = float(previous or 0.0)
    breadth = finalize_breadth(counts, ad_line_start=start)
    if replace:
        await session.execute(delete(MarketBreadthDaily))
    else:
        await session.execute(delete(MarketBreadthDaily).where(MarketBreadthDaily.date >= first))
    rows = breadth.to_dicts()
    for i in range(0, len(rows), 2000):
        await session.execute(insert(MarketBreadthDaily).values(rows[i : i + 2000]))
    await session.commit()


async def _store_group_ranks(session: AsyncSession, collected: _Collected, *, replace: bool) -> int:
    if not collected.members:
        return 0
    ranked = rank_groups(pl.concat(collected.members))
    if ranked.is_empty():
        return 0
    first = _date_bound(ranked[DATE])
    history = ranked.select("group_id", DATE, "rank")
    if not replace:
        earlier = await read_frame(
            "SELECT group_id, date, rank FROM group_rank_history "
            f"WHERE date < '{first}' AND date >= '{sessions_back(first, 30)}'"
        )
        if not earlier.is_empty():
            earlier = earlier.cast({"group_id": pl.Int32, "rank": pl.Int16})
            history = pl.concat([earlier, history])
    trend = add_rank_trend(history).select("group_id", DATE, "rank_change_4w")
    final = ranked.join(trend, on=["group_id", DATE], how="left").cast(
        {
            "group_id": pl.Int32,
            "rank": pl.Int16,
            "members": pl.Int32,
            "tt_passing": pl.Int32,
            "new_highs": pl.Int32,
            "rank_change_4w": pl.Int16,
            "median_rs": pl.Float64,
        }
    )
    if replace:
        await session.execute(delete(GroupRankDaily))
    else:
        await session.execute(delete(GroupRankDaily).where(GroupRankDaily.date >= first))
    await session.commit()
    columns = [
        "group_id",
        DATE,
        "rank",
        "score",
        "members",
        "median_rs",
        "return_3m",
        "return_6m",
        "tt_passing",
        "new_highs",
        "rank_change_4w",
    ]
    await append_frame("group_rank_history", final.select(columns))
    return int(final["group_id"].n_unique())


# --- Regime -----------------------------------------------------------------------------------


async def update_regime(
    session: AsyncSession, settings: AppSettings, through: date
) -> RegimeDay | None:
    """Recompute the whole regime history (cheap: a few thousand rows per index) so it always
    reflects the current settings. Returns the latest overall market day."""
    ids = dict(
        (
            await session.execute(
                select(Ticker.symbol, Ticker.id).where(
                    Ticker.symbol.in_(REGIME_INDEXES), Ticker.is_benchmark
                )
            )
        ).all()
    )
    breadth = {
        day: pct
        for day, pct in (
            await session.execute(
                select(MarketBreadthDaily.date, MarketBreadthDaily.pct_above_50).where(
                    MarketBreadthDaily.date <= through
                )
            )
        ).all()
        if pct is not None
    }
    per_index: dict[str, list[RegimeDay]] = {}
    for symbol, tid in ids.items():
        frame = await read_frame(
            "SELECT b.date, b.high, b.low, b.close, b.volume, i.ema21, i.sma50, i.sma200 "
            "FROM daily_bars b JOIN indicators_daily i ON i.ticker_id = b.ticker_id "
            f"AND i.date = b.date WHERE b.ticker_id = {int(tid)} AND b.date <= '{through}' "
            "ORDER BY b.date"
        )
        if not frame.is_empty():
            per_index[symbol] = compute_index_regime(symbol, frame, settings, breadth)
    considered = ["SPY", "QQQ"] + (["IWM"] if settings.small_cap_mode else [])
    market = combine_market({s: d for s, d in per_index.items() if s in considered})

    await session.execute(delete(MarketRegimeDaily))
    rows = [d.as_row() for days in (*per_index.values(), market) for d in days]
    for i in range(0, len(rows), 1000):
        await session.execute(insert(MarketRegimeDaily).values(rows[i : i + 1000]))
    await session.commit()
    return market[-1] if market else None


# --- Orchestration ----------------------------------------------------------------------------


async def run_analytics(
    session: AsyncSession,
    settings: AppSettings,
    *,
    through: date,
    force_full: bool = False,
    stats: dict[str, object] | None = None,
) -> AnalyticsResult:
    started = time.perf_counter()
    result = AnalyticsResult()
    tickers = await _tickers(session)
    result.tickers = len(tickers)
    if not tickers:
        if stats is not None:
            stats.update(result.stats())
        return result

    await _ensure_indicator_constraints(session)
    group_ids = await sync_groups(session, tickers, settings)
    result.groups = len(set(group_ids.values()))
    last_computed = await session.scalar(select(func.max(IndicatorDaily.date)))
    stale = [t for t in tickers if t.stale]

    if force_full or last_computed is None or len(stale) > FULL_REBUILD_STALE_SHARE * len(tickers):
        result.mode = "full"
        collected = await _full_rebuild(session, tickers, through, settings, group_ids, result)
        await _store_breadth(session, collected, replace=True)
        await _store_group_ranks(session, collected, replace=True)
    else:
        result.mode = "incremental"
        if stale:
            await _recompute(session, stale, tickers, min(through, last_computed), settings, result)
        new_dates = [
            d
            for (d,) in (
                await session.execute(
                    text(
                        "SELECT DISTINCT date FROM daily_bars WHERE date > :after "
                        "AND date <= :through ORDER BY date"
                    ),
                    {"after": last_computed, "through": through},
                )
            ).all()
        ]
        result.new_dates = new_dates
        if new_dates:
            collected = await _incremental(session, tickers, new_dates, settings, group_ids, result)
            await _store_breadth(session, collected, replace=False)
            await _store_group_ranks(session, collected, replace=False)

    market = await update_regime(session, settings, through)
    result.market_state = LABELS[market.state] if market else None
    latest = await session.scalar(
        select(func.max(IndicatorDaily.date)).where(IndicatorDaily.date <= through)
    )
    if latest is not None:
        result.detection, _ = await run_detection(session, settings, latest)
    result.seconds = time.perf_counter() - started
    log.info("analytics.done", **result.stats())
    if stats is not None:
        stats.update(result.stats())
    return result


__all__ = ["MARKET", "AnalyticsResult", "run_analytics", "sync_groups", "update_regime"]

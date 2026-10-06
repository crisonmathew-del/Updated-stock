"""Stage 1 of the backtest: the candidate tape.

Each stock is walked forward one session at a time through the EOD pipeline's own rules:
the liquidity filter, pattern detection (app.patterns.detect), the setups stage
(app.scanner.evaluate) with its Trend Template, grades, group ranks, earnings estimates and
market regime as of that session. The walk keeps what the pipeline keeps in Postgres between
sessions (the `patterns` rows with their ids and retirements, the active setup reloaded from
its stored columns, the patterns of setups that ended), so each session's evaluation gets the
same inputs the nightly scan would have had. `tests/test_backtest_tape.py` checks that the
signals match the pipeline's own log, session by session.

It records, per sensitivity cell (a VCP final-contraction limit × a breakout volume
threshold; detection runs once per VCP limit, the setups walk once per cell):
- candidates: every session a setup ends below its pivot (basing or near pivot) with a trade
  plan: the order a trader would place for the next session (entry, stop, score, grade);
- breakouts: the sessions a setup's breakout was confirmed at the close (the cell's volume
  rule), which stage 2 uses to keep or sell a fresh entry;
- stock_days: the screener's fields on the sessions with candidates (for saved screens);
- signals: every signal the pipeline would have logged (first cell only).
"""

import bisect
import multiprocessing
import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from app.backtest.context import (
    MarketContext,
    StockHistory,
    TickerInfo,
    history_start,
    load_histories,
)
from app.core.logging import get_logger
from app.fundamentals.earnings import estimate_next_release
from app.patterns.detect import detect_variants
from app.patterns.scan import PATTERN_SESSIONS
from app.patterns.types import EVENTS, PatternMatch, PatternType, Status
from app.scanner.evaluate import (
    Detected,
    MarketDay,
    SetupRecord,
    StockDay,
    StockResult,
    Tracked,
    evaluate_stock,
)
from app.scanner.setups import POCKET_PIVOT_SESSIONS, SETUP_SESSIONS, STALE_SESSIONS
from app.scoring.lifecycle import State
from app.settings.schema import AppSettings

log = get_logger(__name__)

PRE_BREAKOUT = frozenset({State.BASING, State.NEAR_PIVOT})
GAP_RECENT_SESSIONS = 5  # app.scanner.screener_rows
CHUNK = 25  # stocks per worker task
LIQUIDITY_CLOSED = "No longer passes the liquidity filters (price, volume or market cap)."


@dataclass(frozen=True)
class Cell:
    """One point of the sensitivity grid: the settings that differ from the run's."""

    vcp_final_max_pct: float
    breakout_volume_pct: float

    def apply(self, base: AppSettings) -> AppSettings:
        return base.model_copy(
            update={
                "vcp_final_contraction_max_pct": self.vcp_final_max_pct,
                "breakout_volume_min_pct_of_avg": self.breakout_volume_pct,
            }
        )

    def as_dict(self) -> dict[str, float]:
        return {"vcp": self.vcp_final_max_pct, "volume": self.breakout_volume_pct}


def base_cell(settings: AppSettings) -> Cell:
    return Cell(settings.vcp_final_contraction_max_pct, settings.breakout_volume_min_pct_of_avg)


# --- What the pipeline keeps in Postgres between sessions -------------------------------------


@dataclass
class PatternRow:
    """A `patterns` row as app.patterns.scan.store_patterns leaves it."""

    id: int
    type: str
    start: date
    pivot: float
    base_low: float | None
    quality: float
    base_number: int | None
    swings: list[tuple[date, float, str]]
    breakout_date: str | None
    status: str
    last_seen: date
    final_contraction: float | None = None

    def tracked(self) -> Tracked:
        """The setup's pattern as app.scanner.setups._record rebuilds it."""
        return Tracked.from_parts(
            self.id,
            self.type,
            self.start,
            self.pivot,
            self.base_low,
            self.quality,
            self.base_number,
            self.swings,
            self.breakout_date,
            self.status,
        )


@dataclass
class PatternStore:
    """One stock's `patterns` rows for one VCP limit."""

    rows: dict[int, PatternRow] = field(default_factory=dict)
    forming: set[int] = field(default_factory=set)  # bases a later run may retire
    pocket_pivots: list[date] = field(default_factory=list)  # starts, sorted
    gaps: list[int] = field(default_factory=list)
    today: list[PatternMatch] = field(default_factory=list)
    today_ids: list[int] = field(default_factory=list)

    def store(
        self,
        matches: list[PatternMatch],
        ids: dict[tuple[str, str, date], int],
        day: date,
        close: float,
    ) -> None:
        """Upsert today's detections, then retire bases that stopped matching."""
        matches.sort(key=lambda m: (str(m.type), m.timeframe, m.start))
        self.today, self.today_ids = matches, []
        for m in matches:
            key = (str(m.type), m.timeframe, m.start)
            pid = ids.setdefault(key, len(ids) + 1)
            self.today_ids.append(pid)
            values = m.as_row()
            contractions = values["contractions"]
            existing = self.rows.get(pid)
            self.rows[pid] = PatternRow(
                pid,
                values["type"],
                m.start,
                values["pivot"],
                values["base_low"],
                values["quality"],
                values["base_number"],
                [
                    (date.fromisoformat(p["date"]), float(p["price"]), str(p["kind"]))
                    for p in values["swings"]
                ],
                (values["details"] or {}).get("breakout_date"),
                values["status"],
                day,
                contractions[-1]["depth_pct"] if contractions else None,
            )
            if m.type not in EVENTS and m.status == Status.FORMING:
                self.forming.add(pid)
            else:
                self.forming.discard(pid)
            if existing is None and m.type == PatternType.POCKET_PIVOT:
                bisect.insort(self.pocket_pivots, m.start)
            if existing is None and m.type == PatternType.EARNINGS_GAP:
                self.gaps.append(pid)
        for pid in list(self.forming):
            row = self.rows[pid]
            if row.last_seen < day:
                low = row.base_low
                row.status = Status.FAILED if low is not None and close < low else Status.EXPIRED
                self.forming.discard(pid)

    def pocket_pivots_since(self, since: date, day: date) -> int:
        return bisect.bisect_right(self.pocket_pivots, day) - bisect.bisect_left(
            self.pocket_pivots, since
        )

    def gap_recent(self, since: date, day: date) -> bool:
        return any(
            since <= self.rows[g].start <= day and self.rows[g].status == Status.FORMING
            for g in self.gaps
        )


@dataclass
class StoredSetup:
    """The columns app.scanner.setups._record reads back the next session, plus what the
    tape reports about the setup."""

    key: int
    kind: str
    state: str
    state_since: date
    first_seen: date
    as_of: date
    close: float
    pattern_id: int | None
    pivot: float | None
    base_low: float | None
    trade_plan: dict[str, Any] | None
    breakout_date: date | None
    best_grade: str | None
    score: float | None = None
    grade: str | None = None
    readiness: float | None = None
    pattern_type: str | None = None
    quality: float | None = None

    @classmethod
    def of(cls, rec: SetupRecord) -> "StoredSetup":
        assert rec.id is not None
        pattern = rec.pattern
        return cls(
            rec.id,
            rec.kind,
            str(rec.state),
            rec.state_since,
            rec.first_seen,
            rec.as_of,
            rec.close,
            None if pattern is None else pattern.id,
            rec.pivot,
            rec.base_low,
            rec.trade_plan,
            rec.breakout_date,
            rec.best_grade,
            None if rec.score is None else rec.score.final,
            None if rec.score is None else rec.score.grade,
            rec.readiness,
            None if pattern is None else pattern.type,
            None if pattern is None else pattern.quality,
        )

    def record(self, ticker_id: int, patterns: PatternStore) -> SetupRecord:
        row = None if self.pattern_id is None else patterns.rows.get(self.pattern_id)
        return SetupRecord(
            id=self.key,
            ticker_id=ticker_id,
            kind=self.kind,
            state=State(self.state),
            state_since=self.state_since,
            first_seen=self.first_seen,
            as_of=self.as_of,
            close=self.close,
            pattern=None if row is None else row.tracked(),
            pivot=self.pivot,
            base_low=self.base_low,
            trade_plan=self.trade_plan,
            breakout_date=self.breakout_date,
            best_grade=self.best_grade,
        )


@dataclass
class CellWalk:
    index: int
    settings: AppSettings
    variant: int  # which PatternStore (VCP limit)
    active: StoredSetup | None = None
    spent: set[int] = field(default_factory=set)
    keys: int = 0


# --- The tape ---------------------------------------------------------------------------------

CANDIDATE_SCHEMA = {
    "cell": pl.Int16,
    "date": pl.Date,
    "ticker_id": pl.Int32,
    "setup": pl.Int32,
    "kind": pl.String,
    "pattern": pl.String,
    "state": pl.String,
    "pivot": pl.Float64,
    "entry": pl.Float64,
    "stop": pl.Float64,
    "risk_pct": pl.Float64,
    "risk_too_wide": pl.Boolean,
    "score": pl.Float64,
    "grade": pl.String,
    "readiness_pct": pl.Float64,
    "quality": pl.Float64,
    "final_contraction": pl.Float64,
    "regime": pl.String,
}
BREAKOUT_SCHEMA = {
    "cell": pl.Int16,
    "date": pl.Date,
    "ticker_id": pl.Int32,
    "setup": pl.Int32,
}
SIGNAL_SCHEMA = {"date": pl.Date, "ticker_id": pl.Int32, "type": pl.String}
STOCK_DAY_SCHEMA = {
    "date": pl.Date,
    "ticker_id": pl.Int32,
    "symbol": pl.String,
    "type": pl.String,
    "sector": pl.String,
    "group": pl.String,
    "group_rank": pl.Int32,
    "close": pl.Float64,
    "change_pct": pl.Float64,
    "volume": pl.Float64,
    "volume_ratio": pl.Float64,
    "dollar_volume": pl.Float64,
    "market_cap": pl.Float64,
    "rs_rating": pl.Int32,
    "rs_line_high": pl.Boolean,
    "rs_ahead": pl.Boolean,
    "stage": pl.Int32,
    "tt_passed": pl.Int32,
    "tt_pass": pl.Boolean,
    "off_high_pct": pl.Float64,
    "above_low_pct": pl.Float64,
    "vs_sma50_pct": pl.Float64,
    "fund_grade": pl.String,
    "pocket_pivot_today": pl.Boolean,
    "earnings_gap_recent": pl.Boolean,
}


@dataclass
class StockTape:
    candidates: list[tuple[Any, ...]] = field(default_factory=list)
    breakouts: list[tuple[Any, ...]] = field(default_factory=list)
    signals: list[tuple[Any, ...]] = field(default_factory=list)
    stock_days: list[tuple[Any, ...]] = field(default_factory=list)
    evaluated: int = 0
    sessions: int = 0


@dataclass
class Tape:
    """Stage 1's output for many stocks, as frames (see the schemas above)."""

    candidates: pl.DataFrame
    breakouts: pl.DataFrame
    signals: pl.DataFrame
    stock_days: pl.DataFrame
    stats: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, parts: Sequence[StockTape]) -> "Tape":
        def frame(rows: list[tuple[Any, ...]], schema: dict[str, Any]) -> pl.DataFrame:
            return pl.DataFrame(rows, schema=schema, orient="row")

        return cls(
            frame([r for p in parts for r in p.candidates], CANDIDATE_SCHEMA),
            frame([r for p in parts for r in p.breakouts], BREAKOUT_SCHEMA),
            frame([r for p in parts for r in p.signals], SIGNAL_SCHEMA),
            frame([r for p in parts for r in p.stock_days], STOCK_DAY_SCHEMA),
            {
                "stocks": len(parts),
                "stock_sessions": sum(p.sessions for p in parts),
                "evaluations": sum(p.evaluated for p in parts),
            },
        )

    @classmethod
    def concat(cls, tapes: Sequence["Tape"]) -> "Tape":
        stats: dict[str, Any] = {}
        for t in tapes:
            for k, v in t.stats.items():
                stats[k] = stats.get(k, 0) + v
        return cls(
            pl.concat([t.candidates for t in tapes])
            if tapes
            else pl.DataFrame(schema=CANDIDATE_SCHEMA),
            pl.concat([t.breakouts for t in tapes])
            if tapes
            else pl.DataFrame(schema=BREAKOUT_SCHEMA),
            pl.concat([t.signals for t in tapes]) if tapes else pl.DataFrame(schema=SIGNAL_SCHEMA),
            pl.concat([t.stock_days for t in tapes])
            if tapes
            else pl.DataFrame(schema=STOCK_DAY_SCHEMA),
            stats,
        )


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100, 2)


def walk_stock(
    stock: StockHistory,
    market: MarketContext,
    base: AppSettings,
    cells: Sequence[Cell],
    start: date,
    end: date,
) -> StockTape:
    """Walk one stock from `start` to `end` (sessions) for every cell. The first cell's
    signals are logged."""
    tid = stock.ticker_id
    info = market.tickers[tid]
    vcps = sorted({c.vcp_final_max_pct for c in cells})
    variants = [base.model_copy(update={"vcp_final_contraction_max_pct": v}) for v in vcps]
    walks = [
        CellWalk(n, c.apply(base), vcps.index(c.vcp_final_max_pct)) for n, c in enumerate(cells)
    ]
    stores = [PatternStore() for _ in vcps]
    ids: dict[tuple[str, str, date], int] = {}
    release_days = [d for d, _ in stock.releases]
    reported: list[date] = sorted(set(release_days))
    days = stock.bars.days
    tape = StockTape()

    for day in market.walk(start, end):
        k = int(days.searchsorted(np.datetime64(day)))
        i = k if k < len(stock.bars) and stock.bars.dates[k] == day else None
        scanned = i is not None and bool(stock.liquid[i])
        if not scanned:
            has_bar = day in stock.bar_days
            for w in walks:
                _close_unscanned(w, day, has_bar, market)
            continue
        assert i is not None
        tape.sessions += 1
        window = stock.window(market.back(day, PATTERN_SESSIONS), day)
        releases = stock.releases[: bisect.bisect_right(release_days, day)]
        found = detect_variants(
            window, variants, last_week_complete=day in market.week_ends, releases=releases
        )
        close = float(window.close[-1])
        for store, matches in zip(stores, found, strict=True):
            store.store(matches, ids, day, close)

        tech = stock.technicals(i)
        common: dict[str, Any] | None = None
        market_day = market.regime.get(day, MarketDay())
        for w in walks:
            store = stores[w.variant]
            if w.active is None and not store.today and not tech.tt_pass:
                continue
            if common is None:
                common = _common(stock, i, day, base, info, market, reported)
            s = common
            stock_day = StockDay(
                ticker_id=tid,
                bars=s["bars"],
                tech=tech,
                fundamentals_grade=s["grade"],
                insider_cluster=s["cluster"],
                group_name=info.group_name,
                group_rank=s["rank"],
                groups_ranked=s["ranked"],
                next_earnings=s["estimate"],
                sessions_to_earnings=s["distance"],
                pocket_pivots_recent=store.pocket_pivots_since(s["since"], day),
                detected=tuple(
                    Detected(pid, m) for pid, m in zip(store.today_ids, store.today, strict=True)
                ),
                spent=frozenset(w.spent),
            )
            active = None if w.active is None else w.active.record(tid, store)
            result = evaluate_stock(stock_day, active, market_day, w.settings, day)
            tape.evaluated += 1
            _apply(w, result, day, tid, tape)
            setup = w.active
            if (
                setup is not None
                and setup.state in PRE_BREAKOUT
                and setup.trade_plan is not None
                and setup.pivot is not None
            ):
                plan = setup.trade_plan
                pattern = None if setup.pattern_id is None else store.rows.get(setup.pattern_id)
                tape.candidates.append(
                    (
                        w.index,
                        day,
                        tid,
                        setup.key,
                        setup.kind,
                        setup.pattern_type,
                        setup.state,
                        setup.pivot,
                        float(plan["entry"]),
                        float(plan["stop"]),
                        float(plan["risk_pct"]),
                        bool(plan["risk_too_wide"]),
                        setup.score,
                        setup.grade,
                        setup.readiness,
                        setup.quality,
                        None if pattern is None else pattern.final_contraction,
                        market_day.state,
                    )
                )
                if not tape.stock_days or tape.stock_days[-1][0] != day:
                    tape.stock_days.append(_stock_fields(stock, i, day, info, s, store, market))
    return tape


def _common(
    stock: StockHistory,
    i: int,
    day: date,
    base: AppSettings,
    info: TickerInfo,
    market: MarketContext,
    reported: list[date],
) -> dict[str, Any]:
    """What every cell's evaluation of one session shares (app.scanner.setups' loaders)."""
    grade, cluster = stock.grade(i, day, base)
    estimate = estimate_next_release(reported[: bisect.bisect_right(reported, day)], day)
    if estimate is not None and estimate <= day:
        estimate = None
    rank = None if info.group_id is None else market.group_ranks.get((info.group_id, day))
    return {
        "bars": stock.window(market.back(day, SETUP_SESSIONS), day),
        "grade": grade,
        "cluster": cluster,
        "rank": rank,
        "ranked": market.groups_ranked.get(day) or None,
        "estimate": estimate,
        "distance": None if estimate is None else market.sessions_to(day, estimate),
        "since": market.back(day, POCKET_PIVOT_SESSIONS - 1),
    }


def _apply(w: CellWalk, result: StockResult, day: date, tid: int, tape: StockTape) -> None:
    """Store the evaluation as app.scanner.setups._store would: new setups get a key, ended
    ones add their pattern to the spent set; log signals and confirmed breakouts."""
    new_active: StoredSetup | None = None
    types: set[str] = set()
    for write in result.writes:
        rec = write.record
        if rec.id is None:
            w.keys += 1
            rec.id = w.keys
        if rec.active:
            new_active = StoredSetup.of(rec)
        elif rec.pattern is not None and rec.pattern.id is not None:
            w.spent.add(rec.pattern.id)
        if write.today is not None and write.today.breakout_today:
            tape.breakouts.append((w.index, day, tid, rec.id))
        types.update(s.type for s in write.signals)
    types.update(s.type for s in result.signals)
    w.active = new_active
    if w.index == 0:
        tape.signals.extend((day, tid, t) for t in sorted(types))


def _close_unscanned(w: CellWalk, day: date, has_bar: bool, market: MarketContext) -> None:
    """app.scanner.setups._close_unscanned: a setup whose stock left the scan closes."""
    setup = w.active
    if setup is None:
        return
    if not has_bar and setup.as_of > market.back(day, STALE_SESSIONS):
        return
    if setup.pattern_id is not None:
        w.spent.add(setup.pattern_id)
    w.active = None


def _stock_fields(
    stock: StockHistory,
    i: int,
    day: date,
    info: TickerInfo,
    shared: dict[str, Any],
    store: PatternStore,
    market: MarketContext,
) -> tuple[Any, ...]:
    """The screener's stock-level fields on `day` (app.scanner.screener_rows), point in
    time: market cap from the share count filed by then."""
    c = stock.columns
    close = float(stock.bars.close[i])
    cap = float(stock.market_cap[i])
    ratio = c["volume_ratio"][i]
    since = market.back(day, GAP_RECENT_SESSIONS - 1)
    return (
        day,
        stock.ticker_id,
        info.symbol,
        info.type,
        info.sector,
        info.group_name,
        shared["rank"],
        close,
        _pct(close, c["prev_close"][i]),  # type: ignore[arg-type]
        float(stock.bars.volume[i]),
        None if ratio is None else round(float(ratio), 2),  # type: ignore[arg-type]
        c["dollar_volume"][i],
        None if cap != cap else cap,
        c["rs_rating"][i],
        bool(c["rs_line_high_52w"][i]),
        bool(c["rs_new_high_ahead"][i]),
        c["stage"][i],
        c["tt_passed"][i],
        bool(c["tt_pass"][i]),
        _pct(close, c["high_52w"][i]),  # type: ignore[arg-type]
        _pct(close, c["low_52w"][i]),  # type: ignore[arg-type]
        _pct(close, c["sma50"][i]),  # type: ignore[arg-type]
        shared["grade"],
        day in store.pocket_pivots,
        store.gap_recent(since, day),
    )


# --- Many stocks, in worker processes ---------------------------------------------------------

_job: dict[str, Any] = {}


def _init(
    market: MarketContext, base: AppSettings, cells: list[Cell], start: date, end: date
) -> None:
    _job.update(market=market, base=base, cells=cells, start=start, end=end)


def walk_chunk(ticker_ids: list[int]) -> Tape:
    """Load and walk a batch of stocks (in a worker, after `_init`)."""
    market: MarketContext = _job["market"]
    start, end = _job["start"], _job["end"]
    histories = load_histories(ticker_ids, market, _job["base"], history_start(start), end)
    return Tape.of(
        [walk_stock(h, market, _job["base"], _job["cells"], start, end) for h in histories]
    )


def tape_workers(stocks: int) -> int:
    from app.core.config import get_settings

    configured = get_settings().pattern_workers
    return min(max(1, stocks // CHUNK), configured or max(1, (os.cpu_count() or 2) - 1))


def build_tape(
    market: MarketContext,
    base: AppSettings,
    cells: list[Cell],
    start: date,
    end: date,
    ticker_ids: Sequence[int] | None = None,
    *,
    workers: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Tape:
    """Walk every stock (or `ticker_ids`) in worker processes; `progress(done, total)` is
    called as batches finish. Synchronous: run it in a thread from async code."""
    started = time.perf_counter()
    ids = sorted(ticker_ids if ticker_ids is not None else market.tickers)
    chunks = [ids[k : k + CHUNK] for k in range(0, len(ids), CHUNK)]
    count = workers if workers is not None else tape_workers(len(ids))
    parts: list[Tape] = []
    done = 0
    if count <= 1:
        _init(market, base, cells, start, end)
        for chunk in chunks:
            parts.append(walk_chunk(chunk))
            done += len(chunk)
            if progress:
                progress(done, len(ids))
    else:
        context = multiprocessing.get_context("forkserver")
        context.set_forkserver_preload(["app.backtest.tape", "polars", "numpy"])
        with ProcessPoolExecutor(
            max_workers=count,
            mp_context=context,
            initializer=_init,
            initargs=(market, base, cells, start, end),
        ) as pool:
            futures: dict[Future[Tape], int] = {
                pool.submit(walk_chunk, chunk): len(chunk) for chunk in chunks
            }
            for future in _as_completed(futures):
                parts.append(future.result())
                done += futures[future]
                if progress:
                    progress(done, len(ids))
    tape = Tape.concat(parts)
    sort = ["cell", "date", "ticker_id"]
    tape.candidates = tape.candidates.sort(sort)
    tape.breakouts = tape.breakouts.sort(sort)
    tape.signals = tape.signals.sort(["date", "ticker_id", "type"])
    tape.stock_days = tape.stock_days.unique(["date", "ticker_id"]).sort(["date", "ticker_id"])
    tape.stats["seconds"] = round(time.perf_counter() - started, 1)
    log.info("backtest.tape_built", **tape.stats, cells=len(cells))
    return tape


def _as_completed(futures: dict[Future[Tape], int]) -> Any:
    from concurrent.futures import as_completed

    return as_completed(futures)

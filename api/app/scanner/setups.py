"""The setups stage of the EOD scan, for one session: load what was known at its close, run
app.scanner.evaluate for every stock with a setup or a candidate (a detection, or a trend
leader), store setups, transitions and signals, then log a market regime change.

Sessions are processed in order and only forward (app.scanner.daily). Re-running the latest
processed session is safe: `rewind` restores every setup from the snapshot taken before that
session was evaluated, deletes the setups it opened and the transitions it recorded. Signals
are immutable: a re-run inserts nothing new for a (session, type, stock) already logged.
"""

import json
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

import polars as pl
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import previous_session, sessions_back, sessions_between
from app.core.logging import get_logger
from app.data.loaders import read_frame
from app.fundamentals.earnings import estimate_next_release
from app.market.regime import LABELS as REGIME_LABELS
from app.market.regime import MARKET, RegimeState
from app.models import (
    FundamentalGrade,
    MarketRegimeDaily,
    Pattern,
    Setup,
    SetupTransition,
    Signal,
)
from app.patterns.bars import INDICATORS, Bars
from app.patterns.scan import PatternRun
from app.patterns.types import PatternMatch
from app.scanner.evaluate import (
    Detected,
    MarketDay,
    SetupRecord,
    SignalDraft,
    StockDay,
    StockResult,
    Technicals,
    Tracked,
    evaluate_stock,
)
from app.scoring.lifecycle import State
from app.scoring.trend_template import evaluate_trend_template
from app.settings.schema import AppSettings

log = get_logger(__name__)

# Bars each evaluation sees: the longest base (65 weeks) for the in-base red flags, plus room.
SETUP_SESSIONS = 340
POCKET_PIVOT_SESSIONS = 10
STALE_SESSIONS = 5  # an active setup without a bar for this long is closed
CHUNK = 500

# Everything a session's evaluation can change on a setup, restored by `rewind`.
LIFECYCLE_COLUMNS = (
    "kind",
    "pattern_id",
    "pattern_type",
    "state",
    "state_since",
    "active",
    "as_of",
    "close",
    "pivot",
    "base_low",
    "readiness_pct",
    "score",
    "raw_score",
    "grade",
    "regime_multiplier",
    "penalties",
    "components",
    "red_flags",
    "trade_plan",
    "breakout_date",
    "best_grade",
    "closed_on",
    "closed_reason",
)


def _ids(values: Iterable[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


# --- Re-runs ----------------------------------------------------------------------------------


async def rewind(session: AsyncSession, as_of: date) -> int:
    """Undo the evaluation of `as_of` and any later session. Returns the setups restored."""
    await session.execute(text("DELETE FROM setups WHERE first_seen >= :d"), {"d": as_of})
    await session.execute(
        text("DELETE FROM setup_transitions WHERE recorded_on >= :d"), {"d": as_of}
    )
    columns = ", ".join(LIFECYCLE_COLUMNS)
    values = ", ".join(f"p.{c}" for c in LIFECYCLE_COLUMNS)
    result = await session.execute(
        text(
            f"UPDATE setups s SET ({columns}) = ({values}), previous = NULL "
            "FROM (SELECT (jsonb_populate_record(NULL::setups, x.previous)).* "
            "FROM setups x WHERE x.as_of >= :d AND x.previous IS NOT NULL) p "
            "WHERE s.id = p.id"
        ),
        {"d": as_of},
    )
    await session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _snapshot(session: AsyncSession, as_of: date) -> None:
    """Keep each active setup as it is before `as_of` is evaluated."""
    await session.execute(
        text(
            "UPDATE setups SET previous = to_jsonb(setups) - 'previous' WHERE active AND as_of < :d"
        ),
        {"d": as_of},
    )


# --- Loading ----------------------------------------------------------------------------------


async def _technicals(
    ids: Sequence[int], as_of: date, settings: AppSettings
) -> dict[int, Technicals]:
    if not ids:
        return {}
    listed = _ids(ids)
    frame = await read_frame(
        "SELECT i.ticker_id, b.close, i.ema21, i.sma50, i.sma150, i.sma200, i.low_52w, "
        "i.high_52w, i.rs_rating, i.stage, i.rs_line_high_52w, i.rs_new_high_ahead, "
        "i.rs_slope_63, i.up_down_volume_50, i.volume_ratio "
        "FROM indicators_daily i JOIN daily_bars b ON b.ticker_id = i.ticker_id "
        f"AND b.date = i.date WHERE i.date = '{as_of.isoformat()}' AND i.ticker_id IN ({listed})"
    )
    if frame.is_empty():
        return {}
    ago = sessions_back(as_of, settings.ma200_uptrend_lookback_days)
    earlier = await read_frame(
        "SELECT ticker_id, sma200 AS sma200_ago FROM indicators_daily "
        f"WHERE date = '{ago.isoformat()}' AND ticker_id IN ({listed})"
    )
    before = await read_frame(
        "SELECT ticker_id, rs_new_high_ahead AS ahead_before FROM indicators_daily "
        f"WHERE date = '{previous_session(as_of).isoformat()}' AND ticker_id IN ({listed})"
    )
    for extra in (earlier, before):
        if not extra.is_empty():
            frame = frame.join(extra, on="ticker_id", how="left")
    if "sma200_ago" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("sma200_ago"))
    if "ahead_before" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.Boolean).alias("ahead_before"))
    has_history = frame["sma200"].is_not_null()
    frame = evaluate_trend_template(frame, settings).with_columns(has_history.alias("has_200"))
    out = {}
    for r in frame.iter_rows(named=True):
        out[int(r["ticker_id"])] = Technicals(
            tt_passed=int(r["tt_passed"]) if r["has_200"] else None,
            tt_pass=bool(r["tt_pass"]),
            stage=None if r["stage"] is None else int(r["stage"]),
            rs_rating=None if r["rs_rating"] is None else int(r["rs_rating"]),
            rs_line_high_52w=bool(r["rs_line_high_52w"]),
            rs_new_high_ahead=bool(r["rs_new_high_ahead"]),
            rs_new_high_ahead_before=bool(r["ahead_before"]),
            rs_slope_63=_float(r["rs_slope_63"]),
            up_down_volume=_float(r["up_down_volume_50"]),
            volume_ratio=_float(r["volume_ratio"]),
            ema21=_float(r["ema21"]),
            sma50=_float(r["sma50"]),
        )
    return out


async def _bars(ids: Sequence[int], as_of: date) -> dict[int, Bars]:
    start = sessions_back(as_of, SETUP_SESSIONS)
    columns = ", ".join(f"i.{c}" for c in INDICATORS)
    out: dict[int, Bars] = {}
    for first in range(0, len(ids), CHUNK):
        frame = await read_frame(
            "SELECT b.ticker_id, b.date, b.open, b.high, b.low, b.close, b.volume, "
            f"{columns} FROM daily_bars b JOIN indicators_daily i "
            "ON i.ticker_id = b.ticker_id AND i.date = b.date "
            f"WHERE b.ticker_id IN ({_ids(ids[first : first + CHUNK])}) "
            f"AND b.date BETWEEN '{start.isoformat()}' AND '{as_of.isoformat()}' "
            "ORDER BY b.ticker_id, b.date"
        )
        if frame.is_empty():
            continue
        for (tid,), rows in frame.partition_by("ticker_id", as_dict=True).items():
            bars = Bars.from_frame(rows)
            if len(bars) and bars.dates[-1] == as_of:
                out[int(tid)] = bars
    return out


def _swings(raw: Any) -> list[tuple[date, float, str]]:
    items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return [(date.fromisoformat(p["date"]), float(p["price"]), str(p["kind"])) for p in items]


def _record(setup: Setup, pattern: Pattern | None) -> SetupRecord:
    tracked = None
    if pattern is not None:
        tracked = Tracked.from_parts(
            pattern.id,
            pattern.type,
            pattern.start_date,
            pattern.pivot,
            pattern.base_low,
            pattern.quality,
            pattern.base_number,
            _swings(pattern.swings),
            (pattern.details or {}).get("breakout_date"),
            pattern.status,
        )
    return SetupRecord(
        id=setup.id,
        ticker_id=setup.ticker_id,
        kind=setup.kind,
        state=State(setup.state),
        state_since=setup.state_since,
        first_seen=setup.first_seen,
        as_of=setup.as_of,
        close=setup.close,
        pattern=tracked,
        pivot=setup.pivot,
        base_low=setup.base_low,
        trade_plan=setup.trade_plan,
        breakout_date=setup.breakout_date,
        best_grade=setup.best_grade,
    )


async def _active(session: AsyncSession) -> dict[int, SetupRecord]:
    rows = await session.execute(
        select(Setup, Pattern)
        .outerjoin(Pattern, Pattern.id == Setup.pattern_id)
        .where(Setup.active)
    )
    return {s.ticker_id: _record(s, p) for s, p in rows.all()}


async def _pattern_ids(ids: Sequence[int], as_of: date) -> dict[tuple[int, str, str, date], int]:
    if not ids:
        return {}
    frame = await read_frame(
        "SELECT id, ticker_id, type, timeframe, start_date FROM patterns "
        f"WHERE ticker_id IN ({_ids(ids)}) AND last_seen = '{as_of.isoformat()}'"
    )
    return {
        (int(t), str(kind), str(tf), start): int(pid)
        for pid, t, kind, tf, start in frame.iter_rows()
    }


async def _spent(session: AsyncSession, ids: Sequence[int]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    if not ids:
        return out
    rows = await session.execute(
        select(Setup.ticker_id, Setup.pattern_id).where(
            Setup.ticker_id.in_(ids), Setup.active.is_(False), Setup.pattern_id.is_not(None)
        )
    )
    for tid, pid in rows.all():
        if pid is not None:
            out[int(tid)].add(int(pid))
    return out


async def _grades(
    session: AsyncSession, ids: Sequence[int], as_of: date
) -> dict[int, tuple[str | None, bool]]:
    rows = await session.execute(
        select(
            FundamentalGrade.ticker_id, FundamentalGrade.grade, FundamentalGrade.components
        ).where(FundamentalGrade.date == as_of, FundamentalGrade.ticker_id.in_(ids))
    )
    out = {}
    for tid, grade, components in rows.all():
        cluster = any(
            c.get("key") == "insider_bonus" and c.get("status") == "pass" for c in components or []
        )
        out[int(tid)] = (grade, cluster)
    return out


async def _groups(
    session: AsyncSession, ids: Sequence[int], as_of: date
) -> tuple[dict[int, tuple[str | None, int | None]], int]:
    frame = await read_frame(
        "SELECT t.id, g.name, r.rank FROM tickers t "
        "LEFT JOIN industry_groups g ON g.id = t.industry_group_id "
        "LEFT JOIN group_rank_history r ON r.group_id = t.industry_group_id "
        f"AND r.date = '{as_of.isoformat()}' WHERE t.id IN ({_ids(ids)})"
    )
    out = {
        int(tid): (None if name is None else str(name), None if rank is None else int(rank))
        for tid, name, rank in frame.iter_rows()
    }
    ranked = await session.scalar(
        text("SELECT count(*) FROM group_rank_history WHERE date = :d"), {"d": as_of}
    )
    return out, int(ranked or 0)


async def _earnings(ids: Sequence[int], as_of: date) -> dict[int, tuple[date, int]]:
    frame = await read_frame(
        "SELECT ticker_id, report_date FROM earnings_calendar WHERE status = 'reported' "
        f"AND report_date <= '{as_of.isoformat()}' AND ticker_id IN ({_ids(ids)})"
    )
    reported: dict[int, list[date]] = defaultdict(list)
    for tid, day in frame.iter_rows():
        reported[int(tid)].append(day)
    out = {}
    distance: dict[date, int] = {}
    for tid, days in reported.items():
        estimate = estimate_next_release(days, as_of)
        if estimate is None or estimate <= as_of:
            continue
        if estimate not in distance:
            distance[estimate] = len(sessions_between(as_of, estimate)) - 1
        out[tid] = (estimate, distance[estimate])
    return out


async def _pocket_pivots(ids: Sequence[int], as_of: date) -> dict[int, int]:
    since = sessions_back(as_of, POCKET_PIVOT_SESSIONS - 1)
    frame = await read_frame(
        "SELECT ticker_id, count(*) AS n FROM patterns WHERE type = 'pocket_pivot' "
        f"AND start_date >= '{since.isoformat()}' AND start_date <= '{as_of.isoformat()}' "
        f"AND first_detected <= '{as_of.isoformat()}' AND ticker_id IN ({_ids(ids)}) "
        "GROUP BY ticker_id"
    )
    return {int(t): int(n) for t, n in frame.iter_rows()}


async def _market(session: AsyncSession, as_of: date) -> tuple[MarketDay, MarketRegimeDaily | None]:
    row = await session.get(MarketRegimeDaily, (as_of, MARKET))
    if row is None:
        return MarketDay(), None
    return MarketDay(row.state, REGIME_LABELS[RegimeState(row.state)]), row


# --- Storing ----------------------------------------------------------------------------------


def _setup_row(rec: SetupRecord) -> dict[str, Any]:
    score = rec.score
    assert score is not None
    return {
        "ticker_id": rec.ticker_id,
        "pattern_id": None if rec.pattern is None else rec.pattern.id,
        "kind": rec.kind,
        "pattern_type": None if rec.pattern is None else rec.pattern.type,
        "state": str(rec.state),
        "state_since": rec.state_since,
        "active": rec.active,
        "first_seen": rec.first_seen,
        "as_of": rec.as_of,
        "close": rec.close,
        "pivot": rec.pivot,
        "base_low": rec.base_low,
        "readiness_pct": rec.readiness,
        "score": score.final,
        "raw_score": score.raw,
        "grade": score.grade,
        "regime_multiplier": score.multiplier,
        "penalties": score.penalties,
        "components": score.components_json(),
        "red_flags": score.red_flags_json(),
        "trade_plan": rec.trade_plan,
        "breakout_date": rec.breakout_date,
        "best_grade": rec.best_grade,
        "closed_on": rec.closed_on,
        "closed_reason": rec.closed_reason,
    }


def _context(
    rec: SetupRecord | None, stock: StockDay, market: MarketDay, draft: SignalDraft
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "technicals": stock.tech.as_dict(),
        "fundamentals_grade": stock.fundamentals_grade,
        "group": {
            "name": stock.group_name,
            "rank": stock.group_rank,
            "of": stock.groups_ranked,
        },
        "market": {"state": market.state, "label": market.label},
        "next_earnings": None if stock.next_earnings is None else stock.next_earnings.isoformat(),
        **draft.detail,
    }
    if rec is not None and rec.score is not None:
        pattern = rec.pattern
        context["setup"] = {
            "kind": rec.kind,
            "state": str(rec.state),
            "pattern_type": None if pattern is None else pattern.type,
            "pattern_label": None if pattern is None else pattern.label,
            "pattern_quality": None if pattern is None else pattern.quality,
            "first_seen": rec.first_seen.isoformat(),
            "breakout_date": None if rec.breakout_date is None else rec.breakout_date.isoformat(),
            "readiness_pct": rec.readiness,
        }
        context["score"] = {
            "raw": rec.score.raw,
            "multiplier": rec.score.multiplier,
            "penalties": rec.score.penalties,
            "final": rec.score.final,
            "grade": rec.score.grade,
            "coverage_pct": rec.score.coverage_pct,
            "regime_note": rec.score.regime_note,
            "components": rec.score.components_json(),
            "red_flags": rec.score.red_flags_json(),
        }
        context["trade_plan"] = rec.trade_plan
    return context


def _signal_row(
    as_of: date,
    ticker_id: int,
    rec: SetupRecord | None,
    stock: StockDay,
    market: MarketDay,
    draft: SignalDraft,
) -> dict[str, Any]:
    plan = rec.trade_plan if rec is not None else None
    score = rec.score if rec is not None else None
    return {
        "date": as_of,
        "type": draft.type,
        "ticker_id": ticker_id,
        "setup_id": None if rec is None else rec.id,
        "summary": draft.summary,
        "price": float(stock.bars.close[stock.bars.last]),
        "pivot": None if rec is None else rec.pivot,
        "entry": None if plan is None else plan["entry"],
        "stop": None if plan is None else plan["stop"],
        "score": None if score is None else score.final,
        "grade": None if score is None else score.grade,
        "context": _context(rec, stock, market, draft),
    }


async def _insert_signals(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), CHUNK):
        stmt = pg_insert(Signal).values(rows[start : start + CHUNK])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["date", "type", "ticker_id"],
                set_={"setup_id": stmt.excluded.setup_id},
                where=Signal.setup_id.is_(None),
            )
        )


async def _store(
    session: AsyncSession,
    as_of: date,
    results: list[tuple[StockDay, StockResult]],
    market: MarketDay,
) -> Counter[str]:
    writes = [w for _, result in results for w in result.writes]
    new = [w for w in writes if w.record.id is None]
    existing = [{"id": w.record.id, **_setup_row(w.record)} for w in writes if w.record.id]
    if existing:  # first: a closing setup must be inactive before its successor is inserted
        await session.execute(update(Setup), existing)
    if new:
        ids = await session.scalars(
            insert(Setup).returning(Setup.id, sort_by_parameter_order=True),
            [_setup_row(w.record) for w in new],
        )
        for w, setup_id in zip(new, ids.all(), strict=True):
            w.record.id = int(setup_id)
    transitions = [
        {
            "setup_id": w.record.id,
            "date": x.date,
            "from_state": x.from_state,
            "to_state": x.to_state,
            "reason": x.reason,
            "recorded_on": as_of,
        }
        for w in writes
        for x in w.transitions
    ]
    for start in range(0, len(transitions), CHUNK):
        await session.execute(insert(SetupTransition), transitions[start : start + CHUNK])
    signals = []
    for stock, result in results:
        for w in result.writes:
            signals += [
                _signal_row(as_of, stock.ticker_id, w.record, stock, market, d) for d in w.signals
            ]
        signals += [
            _signal_row(as_of, stock.ticker_id, None, stock, market, d) for d in result.signals
        ]
    await _insert_signals(session, signals)
    return Counter(row["type"] for row in signals)


async def _close_unscanned(
    session: AsyncSession, as_of: date, active: dict[int, SetupRecord], scanned: set[int]
) -> int:
    """Close active setups of stocks the scan no longer covers: dropped below the liquidity
    filters (a bar today but not scanned), or without price data for STALE_SESSIONS."""
    missing = [tid for tid in active if tid not in scanned]
    if not missing:
        return 0
    with_bar = {
        int(t)
        for (t,) in (
            await session.execute(
                text(
                    "SELECT ticker_id FROM daily_bars WHERE date = :d "
                    f"AND ticker_id IN ({_ids(missing)})"
                ),
                {"d": as_of},
            )
        ).all()
    }
    stale_before = sessions_back(as_of, STALE_SESSIONS)
    closed = 0
    for tid in missing:
        rec = active[tid]
        if tid in with_bar:
            reason = "No longer passes the liquidity filters (price, volume or market cap)."
        elif rec.as_of <= stale_before:
            reason = f"No price data since {rec.as_of.isoformat()}."
        else:
            continue
        await session.execute(
            update(Setup)
            .where(Setup.id == rec.id)
            .values(active=False, closed_on=as_of, closed_reason=reason, as_of=as_of)
        )
        closed += 1
    return closed


async def _regime_signal(session: AsyncSession, as_of: date, row: MarketRegimeDaily | None) -> int:
    if row is None or row.changed_from is None:
        return 0
    spy_close = await session.scalar(
        text(
            "SELECT b.close FROM daily_bars b JOIN tickers t ON t.id = b.ticker_id "
            "WHERE t.symbol = 'SPY' AND t.is_benchmark AND b.date = :d"
        ),
        {"d": as_of},
    )
    old = REGIME_LABELS[RegimeState(row.changed_from)]
    new = REGIME_LABELS[RegimeState(row.state)]
    reasons = [str(r) for r in row.reasons or []]
    await _insert_signals(
        session,
        [
            {
                "date": as_of,
                "type": "regime_change",
                "ticker_id": None,
                "setup_id": None,
                "summary": f"Market regime: {old} → {new}. " + " ".join(reasons[:1]),
                "price": _float(spy_close),
                "pivot": None,
                "entry": None,
                "stop": None,
                "score": None,
                "grade": None,
                "context": {
                    "from": row.changed_from,
                    "to": row.state,
                    "reasons": reasons,
                    "distribution_days": row.distribution_days,
                    "last_ftd_date": None
                    if row.last_ftd_date is None
                    else row.last_ftd_date.isoformat(),
                },
            }
        ],
    )
    return 1


# --- The stage --------------------------------------------------------------------------------


async def run_setups(
    session: AsyncSession,
    settings: AppSettings,
    as_of: date,
    run: PatternRun,
) -> dict[str, Any]:
    """Evaluate `as_of` for every scanned stock (`run.closes`: the whole liquid universe) with
    a setup or a candidate."""
    started = time.perf_counter()
    if await session.scalar(select(func.count()).select_from(Setup).where(Setup.as_of >= as_of)):
        await rewind(session, as_of)
    await _snapshot(session, as_of)
    active = await _active(session)
    scanned = set(run.closes)
    tech = await _technicals(sorted(scanned), as_of, settings)
    by_ticker: dict[int, list[PatternMatch]] = defaultdict(list)
    for tid, m in run.matches:
        by_ticker[tid].append(m)
    candidates = sorted(
        tid
        for tid in scanned
        if tid in active or by_ticker.get(tid) or (tid in tech and tech[tid].tt_pass)
    )
    bars = await _bars(candidates, as_of)
    candidates = [tid for tid in candidates if tid in bars and tid in tech]
    pattern_ids = await _pattern_ids(candidates, as_of)
    spent = await _spent(session, candidates)
    grades = await _grades(session, candidates, as_of) if candidates else {}
    groups, groups_ranked = await _groups(session, candidates, as_of) if candidates else ({}, 0)
    earnings = await _earnings(candidates, as_of) if candidates else {}
    pivots = await _pocket_pivots(candidates, as_of) if candidates else {}
    market, regime_row = await _market(session, as_of)

    results: list[tuple[StockDay, StockResult]] = []
    for tid in candidates:
        grade, cluster = grades.get(tid, (None, False))
        group_name, group_rank = groups.get(tid, (None, None))
        next_earnings, sessions_away = earnings.get(tid, (None, None))
        detected = tuple(
            Detected(pattern_ids.get((tid, str(m.type), m.timeframe, m.start)), m)
            for m in by_ticker.get(tid, [])
        )
        stock = StockDay(
            ticker_id=tid,
            bars=bars[tid],
            tech=tech[tid],
            fundamentals_grade=grade,
            insider_cluster=cluster,
            group_name=group_name,
            group_rank=group_rank,
            groups_ranked=groups_ranked or None,
            next_earnings=next_earnings,
            sessions_to_earnings=sessions_away,
            pocket_pivots_recent=pivots.get(tid, 0),
            detected=detected,
            spent=frozenset(spent.get(tid, set())),
        )
        results.append((stock, evaluate_stock(stock, active.get(tid), market, settings, as_of)))

    signals = await _store(session, as_of, results, market)
    dropped = await _close_unscanned(session, as_of, active, scanned)
    signals["regime_change"] += await _regime_signal(session, as_of, regime_row)
    await session.commit()

    states = Counter(str(w.record.state) for _, r in results for w in r.writes if w.record.active)
    opened = sum(1 for _, r in results for w in r.writes if w.record.first_seen == as_of)
    closed = sum(1 for _, r in results for w in r.writes if not w.record.active) + dropped
    stats = {
        "as_of": as_of.isoformat(),
        "evaluated": len(results),
        "active": dict(sorted(states.items())),
        "opened": opened,
        "closed": closed,
        "signals": {k: v for k, v in sorted(signals.items()) if v},
        "seconds": round(time.perf_counter() - started, 1),
    }
    log.info("setups.done", **stats)
    return stats

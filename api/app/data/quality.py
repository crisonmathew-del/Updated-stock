"""Data-quality checks (spec §5.5) and the open-issue register behind the data-health panel.

Each check is a pure function over Polars frames, so it can be tested on synthetic bars. A run
loads recent bars in chunks of tickers, runs every check, then reconciles `data_quality_issues`:
new findings are inserted, repeat findings refreshed, and open findings that no longer occur are
marked resolved.

Severity:
- critical: the dataset as a whole can't be trusted for today's scan (benchmarks missing, EOD
  update not run, a large share of the universe stale).
- warning: one ticker's data looks wrong and should be checked.
- info: worth knowing, usually legitimate (a short history after an IPO, a trading halt).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_back, sessions_between
from app.core.config import get_settings
from app.core.logging import get_logger
from app.data.loaders import load_bars
from app.models import CorporateAction, DataQualityIssue, Ticker
from app.models.ticker import BackfillStatus

log = get_logger(__name__)


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Check(StrEnum):
    MISSING_SESSIONS = "missing_sessions"
    OHLC_INCONSISTENT = "ohlc_inconsistent"
    ZERO_VOLUME = "zero_volume"
    UNEXPLAINED_MOVE = "unexplained_move"
    STALE_TICKER = "stale_ticker"
    DATASET_STALE = "dataset_stale"
    UNIVERSE_STALE = "universe_stale"
    BACKFILL_FAILED = "backfill_failed"
    NO_PRICE_DATA = "no_price_data"
    SHORT_HISTORY = "short_history"
    NOT_BACKFILLED = "not_backfilled"
    SEC_NOT_CONFIGURED = "sec_not_configured"
    UNIVERSE_EMPTY = "universe_empty"


ALL_CHECKS = tuple(Check)

WINDOW_SESSIONS = 756  # three years
RECENT_SESSIONS = 20
STALE_AFTER_SESSIONS = 3
UNIVERSE_STALE_CRITICAL_SHARE = 0.05
MOVE_THRESHOLD = 0.5
SPLIT_TOLERANCE_DAYS = 3
MIN_HISTORY_SESSIONS = 252
CHUNK_TICKERS = 400


@dataclass(frozen=True)
class Issue:
    check: Check
    severity: Severity
    detail: str
    ticker_id: int | None = None
    issue_date: date | None = None
    data: dict[str, Any] = field(default_factory=dict)
    per_date: bool = False

    @property
    def fingerprint(self) -> str:
        parts = [self.check.value, str(self.ticker_id or "-")]
        if self.per_date:
            parts.append(self.issue_date.isoformat() if self.issue_date else "-")
        return ":".join(parts)


# --- Per-ticker checks over bars ------------------------------------------------------------


def check_ohlc(bars: pl.DataFrame) -> list[Issue]:
    bad = bars.filter(
        (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("high") < pl.col("low"))
        | (pl.col("low") <= 0)
    )
    issues = []
    for row in (
        bad.group_by("ticker_id")
        .agg(pl.len().alias("count"), pl.col("date").max().alias("latest"))
        .iter_rows(named=True)
    ):
        issues.append(
            Issue(
                Check.OHLC_INCONSISTENT,
                Severity.WARNING,
                f"{row['count']} bar(s) where high/low don't contain open/close or prices "
                f"aren't positive (latest {row['latest']})",
                ticker_id=row["ticker_id"],
                issue_date=row["latest"],
                data={"count": row["count"]},
            )
        )
    return issues


def check_zero_volume(bars: pl.DataFrame, recent_from: date) -> list[Issue]:
    zero = bars.filter(pl.col("volume") <= 0)
    issues = []
    for row in (
        zero.group_by("ticker_id")
        .agg(pl.len().alias("count"), pl.col("date").max().alias("latest"))
        .iter_rows(named=True)
    ):
        recent = row["latest"] >= recent_from
        issues.append(
            Issue(
                Check.ZERO_VOLUME,
                Severity.WARNING if recent else Severity.INFO,
                f"{row['count']} session(s) with zero volume (latest {row['latest']})"
                + ("; possibly halted or a bad recent bar" if recent else ""),
                ticker_id=row["ticker_id"],
                issue_date=row["latest"],
                data={"count": row["count"]},
            )
        )
    return issues


def check_unexplained_moves(bars: pl.DataFrame, splits: pl.DataFrame) -> list[Issue]:
    """Close-to-close moves over 50% with no split within a few days. Real moves this big do
    happen (takeovers, biotech readouts), so these are flagged for review, not discarded."""
    moves = (
        bars.sort("ticker_id", "date")
        .with_columns(prev_close=pl.col("close").shift(1).over("ticker_id"))
        .with_columns(change=pl.col("close") / pl.col("prev_close") - 1)
        .filter(pl.col("change").abs() > MOVE_THRESHOLD)
    )
    if moves.is_empty():
        return []
    split_days = {(tid, d) for tid, d in splits.select("ticker_id", "ex_date").iter_rows()}
    issues = []
    for row in moves.iter_rows(named=True):
        near_split = any(
            (row["ticker_id"], row["date"] + timedelta(days=offset)) in split_days
            for offset in range(-SPLIT_TOLERANCE_DAYS, SPLIT_TOLERANCE_DAYS + 1)
        )
        if near_split:
            detail = (
                f"{row['change']:+.0%} close-to-close next to a split: history may not be "
                "split-adjusted"
            )
        else:
            detail = f"{row['change']:+.0%} close-to-close with no split on record; verify"
        issues.append(
            Issue(
                Check.UNEXPLAINED_MOVE,
                Severity.WARNING,
                detail,
                ticker_id=row["ticker_id"],
                issue_date=row["date"],
                data={"change": round(row["change"], 4), "near_split": near_split},
                per_date=True,
            )
        )
    return issues


def check_missing_sessions(
    bars: pl.DataFrame, sessions: Sequence[date], benchmark_ids: set[int]
) -> list[Issue]:
    """Sessions with no bar between a ticker's first and last bar in the window."""
    if bars.is_empty():
        return []
    calendar = np.array(sessions, dtype="datetime64[D]")
    session_set = set(sessions)
    spans = bars.group_by("ticker_id").agg(
        pl.col("date").min().alias("first"),
        pl.col("date").max().alias("last"),
        pl.col("date").filter(pl.col("date").is_in(list(session_set))).n_unique().alias("have"),
    )
    issues = []
    for row in spans.iter_rows(named=True):
        lo = int(np.searchsorted(calendar, np.datetime64(row["first"], "D"), side="left"))
        hi = int(np.searchsorted(calendar, np.datetime64(row["last"], "D"), side="right"))
        missing = hi - lo - int(row["have"])
        if missing <= 0:
            continue
        have = set(
            bars.filter(pl.col("ticker_id") == row["ticker_id"]).get_column("date").to_list()
        )
        gaps = [d for d in sessions[lo:hi] if d not in have]
        is_benchmark = row["ticker_id"] in benchmark_ids
        severity = (
            Severity.CRITICAL
            if is_benchmark
            else Severity.WARNING
            if missing > 2
            else Severity.INFO
        )
        issues.append(
            Issue(
                Check.MISSING_SESSIONS,
                severity,
                f"{missing} trading session(s) with no bar between {row['first']} and "
                f"{row['last']} (e.g. {', '.join(d.isoformat() for d in gaps[:3])})",
                ticker_id=row["ticker_id"],
                issue_date=gaps[-1] if gaps else None,
                data={"missing": missing, "sample": [d.isoformat() for d in gaps[:10]]},
            )
        )
    return issues


def check_short_history(bars: pl.DataFrame, listed: dict[int, date | None]) -> list[Issue]:
    counts = bars.group_by("ticker_id").agg(
        pl.len().alias("n"), pl.col("date").min().alias("first")
    )
    issues = []
    for row in counts.filter(pl.col("n") < MIN_HISTORY_SESSIONS).iter_rows(named=True):
        recent_listing = listed.get(row["ticker_id"]) is not None
        issues.append(
            Issue(
                Check.SHORT_HISTORY,
                Severity.INFO,
                f"Only {row['n']} sessions since {row['first']}"
                + (" (listed recently)" if recent_listing else "")
                + "; 52-week and RS metrics need 252",
                ticker_id=row["ticker_id"],
                data={"sessions": row["n"]},
            )
        )
    return issues


# --- Dataset-level checks -------------------------------------------------------------------


@dataclass(frozen=True)
class TickerState:
    id: int
    symbol: str
    is_benchmark: bool
    backfill_status: str
    backfill_error: str | None
    bars_end: date | None
    listed_date: date | None


def check_staleness(
    tickers: Sequence[TickerState], expected_session: date, stale_before: date
) -> list[Issue]:
    issues: list[Issue] = []
    done = [t for t in tickers if t.backfill_status == BackfillStatus.DONE]
    latest = max((t.bars_end for t in done if t.bars_end), default=None)
    if done and (latest is None or latest < expected_session):
        issues.append(
            Issue(
                Check.DATASET_STALE,
                Severity.CRITICAL,
                f"No bars for {expected_session} yet (latest is {latest}). The end-of-day "
                "update hasn't run or failed; check the job history.",
                issue_date=expected_session,
                data={"latest": latest.isoformat() if latest else None},
            )
        )
    stale = [t for t in done if t.bars_end is not None and t.bars_end < stale_before]
    for t in stale:
        issues.append(
            Issue(
                Check.STALE_TICKER,
                Severity.CRITICAL if t.is_benchmark else Severity.WARNING,
                f"Last bar {t.bars_end}; no data for {STALE_AFTER_SESSIONS}+ sessions. "
                "Possibly halted, renamed or delisted.",
                ticker_id=t.id,
                issue_date=t.bars_end,
            )
        )
    stocks = [t for t in done if not t.is_benchmark]
    stale_stocks = [t for t in stale if not t.is_benchmark]
    if stocks and len(stale_stocks) / len(stocks) > UNIVERSE_STALE_CRITICAL_SHARE:
        issues.append(
            Issue(
                Check.UNIVERSE_STALE,
                Severity.CRITICAL,
                f"{len(stale_stocks)} of {len(stocks)} stocks "
                f"({len(stale_stocks) / len(stocks):.0%}) have no recent bars.",
                data={"stale": len(stale_stocks), "total": len(stocks)},
            )
        )
    return issues


def check_backfill_outcomes(tickers: Sequence[TickerState]) -> list[Issue]:
    if not any(not t.is_benchmark for t in tickers):
        return [
            Issue(
                Check.UNIVERSE_EMPTY,
                Severity.CRITICAL,
                "No stocks in the universe yet. Run the universe build (make universe), which "
                "also starts the backfill.",
            )
        ]
    issues = []
    for t in tickers:
        if t.backfill_status == BackfillStatus.FAILED:
            issues.append(
                Issue(
                    Check.BACKFILL_FAILED,
                    Severity.CRITICAL if t.is_benchmark else Severity.WARNING,
                    f"Backfill failed: {t.backfill_error}. Run the backfill again to retry.",
                    ticker_id=t.id,
                )
            )
        elif t.backfill_status == BackfillStatus.NO_DATA:
            issues.append(
                Issue(
                    Check.NO_PRICE_DATA,
                    Severity.CRITICAL if t.is_benchmark else Severity.INFO,
                    f"The price provider has no data: {t.backfill_error}",
                    ticker_id=t.id,
                )
            )
    pending = [t for t in tickers if t.backfill_status == BackfillStatus.PENDING]
    if pending:
        benchmarks = [t.symbol for t in pending if t.is_benchmark]
        issues.append(
            Issue(
                Check.NOT_BACKFILLED,
                Severity.CRITICAL if benchmarks else Severity.INFO,
                f"{len(pending)} ticker(s) have no history yet"
                + (f", including benchmarks {', '.join(benchmarks)}" if benchmarks else "")
                + ". Run the backfill.",
                data={"count": len(pending), "sample": [t.symbol for t in pending[:20]]},
            )
        )
    return issues


def check_configuration() -> list[Issue]:
    if get_settings().sec_user_agent:
        return []
    return [
        Issue(
            Check.SEC_NOT_CONFIGURED,
            Severity.WARNING,
            "SEC_USER_AGENT is not set, so CIKs, industry codes and market caps aren't "
            "loaded. Set it in .env, e.g. SEC_USER_AGENT='Breakout you@example.com'.",
        )
    ]


# --- Run and reconcile ----------------------------------------------------------------------


async def _ticker_states(session: AsyncSession) -> list[TickerState]:
    rows = await session.execute(
        select(
            Ticker.id,
            Ticker.symbol,
            Ticker.is_benchmark,
            Ticker.backfill_status,
            Ticker.backfill_error,
            Ticker.bars_end,
            Ticker.listed_date,
        ).where(Ticker.active)
    )
    return [TickerState(*row) for row in rows.all()]


async def collect_issues(session: AsyncSession, expected_session: date) -> list[Issue]:
    tickers = await _ticker_states(session)
    window_start = sessions_back(expected_session, WINDOW_SESSIONS)
    sessions = sessions_between(window_start, expected_session)
    recent_from = sessions_back(expected_session, RECENT_SESSIONS)
    stale_before = sessions_back(expected_session, STALE_AFTER_SESSIONS - 1)
    benchmark_ids = {t.id for t in tickers if t.is_benchmark}
    listed = {t.id: t.listed_date for t in tickers}

    issues = check_configuration()
    issues += check_backfill_outcomes(tickers)
    issues += check_staleness(tickers, expected_session, stale_before)

    splits = pl.DataFrame(
        (
            await session.execute(
                select(CorporateAction.ticker_id, CorporateAction.ex_date).where(
                    CorporateAction.kind == "split"
                )
            )
        ).all(),
        schema={"ticker_id": pl.Int32, "ex_date": pl.Date},
        orient="row",
    )
    ids = [t.id for t in tickers if t.backfill_status == BackfillStatus.DONE]
    for offset in range(0, len(ids), CHUNK_TICKERS):
        bars = await load_bars(window_start, expected_session, ids[offset : offset + CHUNK_TICKERS])
        issues += check_ohlc(bars)
        issues += check_zero_volume(bars, recent_from)
        issues += check_unexplained_moves(bars, splits)
        issues += check_missing_sessions(bars, sessions, benchmark_ids)
        issues += check_short_history(bars, listed)
    return issues


async def reconcile(
    session: AsyncSession,
    issues: Iterable[Issue],
    now: datetime,
    checks: Iterable[Check] = ALL_CHECKS,
) -> dict[str, int]:
    """Upsert current findings and resolve open ones that no longer occur."""
    current = {issue.fingerprint: issue for issue in issues}
    rows = [
        {
            "fingerprint": fp,
            "check": i.check.value,
            "severity": i.severity.value,
            "ticker_id": i.ticker_id,
            "issue_date": i.issue_date,
            "detail": i.detail,
            "data": i.data,
            "first_detected_at": now,
            "last_detected_at": now,
        }
        for fp, i in current.items()
    ]
    for start in range(0, len(rows), 2000):
        stmt = insert(DataQualityIssue).values(rows[start : start + 2000])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["fingerprint"],
                index_where=DataQualityIssue.resolved_at.is_(None),
                set_={
                    "severity": stmt.excluded.severity,
                    "issue_date": stmt.excluded.issue_date,
                    "detail": stmt.excluded.detail,
                    "data": stmt.excluded.data,
                    "last_detected_at": stmt.excluded.last_detected_at,
                },
            )
        )
    resolved = await session.execute(
        update(DataQualityIssue)
        .where(
            DataQualityIssue.resolved_at.is_(None),
            DataQualityIssue.check.in_([c.value for c in checks]),
            DataQualityIssue.fingerprint.not_in(list(current) or [""]),
        )
        .values(resolved_at=now)
    )
    await session.commit()
    counts = {s.value: 0 for s in Severity}
    for issue in current.values():
        counts[issue.severity.value] += 1
    counts["resolved"] = int(getattr(resolved, "rowcount", 0) or 0)
    return counts


async def run_quality_checks(
    session: AsyncSession, expected_session: date, stats: dict[str, object] | None = None
) -> dict[str, int]:
    issues = await collect_issues(session, expected_session)
    counts = await reconcile(session, issues, datetime.now(UTC))
    log.info("data_quality.done", session=expected_session, **counts)
    if stats is not None:
        stats.update(session=expected_session.isoformat(), **counts)
    return counts


async def open_issue_counts(session: AsyncSession) -> dict[str, dict[str, int]]:
    """{check: {severity: count}} for open issues."""
    rows = await session.execute(
        select(DataQualityIssue.check, DataQualityIssue.severity, func.count())
        .where(DataQualityIssue.resolved_at.is_(None))
        .group_by(DataQualityIssue.check, DataQualityIssue.severity)
    )
    summary: dict[str, dict[str, int]] = {}
    for check, severity, count in rows.all():
        summary.setdefault(check, {})[severity] = count
    return summary

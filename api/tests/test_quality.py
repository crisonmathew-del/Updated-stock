from collections.abc import Sequence
from datetime import UTC, date, datetime

import polars as pl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_between
from app.data.quality import (
    Check,
    Issue,
    Severity,
    TickerState,
    check_backfill_outcomes,
    check_missing_sessions,
    check_ohlc,
    check_staleness,
    check_unexplained_moves,
    check_zero_volume,
    open_issue_counts,
    reconcile,
)
from app.models import DataQualityIssue

SESSIONS = sessions_between(date(2026, 9, 1), date(2026, 9, 30))  # 21 sessions (Labor Day 7th)


Row = tuple[int, date, float, float, float, float, int]


def frame(rows: Sequence[Row]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "ticker_id": pl.Int32,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
        },
        orient="row",
    )


def clean_bars(ticker_id: int, days: list[date], close: float = 100.0) -> list[Row]:
    return [(ticker_id, d, close, close + 1, close - 1, close, 1_000) for d in days]


NO_SPLITS = pl.DataFrame(schema={"ticker_id": pl.Int32, "ex_date": pl.Date})


def test_clean_bars_raise_nothing() -> None:
    bars = frame(clean_bars(1, SESSIONS))
    assert check_ohlc(bars) == []
    assert check_zero_volume(bars, SESSIONS[-5]) == []
    assert check_unexplained_moves(bars, NO_SPLITS) == []
    assert check_missing_sessions(bars, SESSIONS, set()) == []


def test_ohlc_inconsistencies_are_grouped_per_ticker() -> None:
    rows = clean_bars(1, SESSIONS[:3])
    rows.append((1, SESSIONS[3], 100, 99, 98, 100.5, 10))  # close above high
    rows.append((1, SESSIONS[4], 100, 101, 102, 100, 10))  # low above high
    rows.append((2, SESSIONS[4], 0, 1, 0, 0.5, 10))  # non-positive low
    issues = {i.ticker_id: i for i in check_ohlc(frame(rows))}
    assert issues[1].data == {"count": 2}
    assert issues[1].issue_date == SESSIONS[4]
    assert issues[2].severity is Severity.WARNING


def test_zero_volume_is_a_warning_only_when_recent() -> None:
    old = [(1, SESSIONS[0], 10, 11, 9, 10, 0)]
    recent = [(2, SESSIONS[-1], 10, 11, 9, 10, 0)]
    issues = {i.ticker_id: i for i in check_zero_volume(frame(old + recent), SESSIONS[-5])}
    assert issues[1].severity is Severity.INFO
    assert issues[2].severity is Severity.WARNING


def test_big_moves_are_flagged_and_split_days_explained() -> None:
    rows = clean_bars(1, SESSIONS[:5], close=100) + clean_bars(1, SESSIONS[5:8], close=25)
    rows += clean_bars(2, SESSIONS[:5], close=10) + clean_bars(2, SESSIONS[5:8], close=16)
    splits = pl.DataFrame(
        {"ticker_id": [1], "ex_date": [SESSIONS[5]]},
        schema={"ticker_id": pl.Int32, "ex_date": pl.Date},
    )

    issues = {i.ticker_id: i for i in check_unexplained_moves(frame(rows), splits)}

    assert issues[1].data == {"change": -0.75, "near_split": True}
    assert "may not be split-adjusted" in issues[1].detail
    assert issues[2].data == {"change": 0.6, "near_split": False}
    assert issues[2].fingerprint == f"unexplained_move:2:{SESSIONS[5].isoformat()}"


def test_missing_sessions_name_the_gaps_and_escalate_for_benchmarks() -> None:
    gappy = [d for i, d in enumerate(SESSIONS) if i not in (3, 4, 10)]
    rows = (
        clean_bars(1, gappy) + clean_bars(99, gappy) + clean_bars(2, SESSIONS[:10] + SESSIONS[11:])
    )

    issues = {i.ticker_id: i for i in check_missing_sessions(frame(rows), SESSIONS, {99})}

    assert issues[1].data["missing"] == 3
    assert issues[1].data["sample"] == [
        SESSIONS[3].isoformat(),
        SESSIONS[4].isoformat(),
        SESSIONS[10].isoformat(),
    ]
    assert issues[1].severity is Severity.WARNING
    assert issues[99].severity is Severity.CRITICAL
    assert issues[2].severity is Severity.INFO  # a single gap


def state(
    tid: int,
    status: str = "done",
    bars_end: date | None = SESSIONS[-1],
    benchmark: bool = False,
    error: str | None = None,
) -> TickerState:
    return TickerState(tid, f"T{tid}", benchmark, status, error, bars_end, None)


def test_dataset_and_universe_staleness_are_critical() -> None:
    tickers = [state(i, bars_end=SESSIONS[-3]) for i in range(10)]
    issues = check_staleness(tickers, expected_session=SESSIONS[-1], stale_before=SESSIONS[-2])
    checks = {i.check: i for i in issues if i.ticker_id is None}
    assert checks[Check.DATASET_STALE].severity is Severity.CRITICAL
    assert checks[Check.UNIVERSE_STALE].data == {"stale": 10, "total": 10}


def test_a_few_stale_stocks_are_warnings_but_a_stale_benchmark_is_critical() -> None:
    tickers = [state(i) for i in range(100)]
    tickers.append(state(500, bars_end=SESSIONS[-6]))
    tickers.append(state(600, bars_end=SESSIONS[-6], benchmark=True))
    issues = check_staleness(tickers, SESSIONS[-1], SESSIONS[-2])
    severities = {i.ticker_id: i.severity for i in issues}
    assert severities == {500: Severity.WARNING, 600: Severity.CRITICAL}


def test_backfill_outcomes() -> None:
    issues = check_backfill_outcomes(
        [
            state(1, "failed", error="HTTPError: boom"),
            state(2, "no_data", error="no data"),
            state(3, "pending"),
            state(4, "pending", benchmark=True),
        ]
    )
    by_check = {i.check: i for i in issues}
    assert by_check[Check.BACKFILL_FAILED].severity is Severity.WARNING
    assert by_check[Check.NO_PRICE_DATA].severity is Severity.INFO
    assert by_check[Check.NOT_BACKFILLED].severity is Severity.CRITICAL
    assert "including benchmarks T4" in by_check[Check.NOT_BACKFILLED].detail


@pytest.mark.integration
async def test_reconcile_inserts_refreshes_and_resolves(db: AsyncSession) -> None:
    first = [
        Issue(Check.SEC_NOT_CONFIGURED, Severity.WARNING, "SEC_USER_AGENT is not set"),
        Issue(Check.NOT_BACKFILLED, Severity.INFO, "3 tickers have no history yet"),
    ]
    counts = await reconcile(db, first, datetime(2026, 10, 1, tzinfo=UTC))
    assert counts == {"critical": 0, "warning": 1, "info": 1, "resolved": 0}

    second = [Issue(Check.NOT_BACKFILLED, Severity.INFO, "1 ticker has no history yet")]
    counts = await reconcile(db, second, datetime(2026, 10, 2, tzinfo=UTC))
    assert counts["resolved"] == 1

    rows = {r.check: r for r in (await db.scalars(select(DataQualityIssue))).all()}
    assert rows["sec_not_configured"].resolved_at is not None
    assert rows["not_backfilled"].resolved_at is None
    assert rows["not_backfilled"].detail == "1 ticker has no history yet"
    assert rows["not_backfilled"].first_detected_at.date() == date(2026, 10, 1)
    assert await open_issue_counts(db) == {"not_backfilled": {"info": 1}}

    # A problem that comes back after being resolved opens a fresh issue.
    await reconcile(db, first, datetime(2026, 10, 3, tzinfo=UTC))
    sec_rows = (
        await db.scalars(
            select(DataQualityIssue).where(DataQualityIssue.check == "sec_not_configured")
        )
    ).all()
    assert len(sec_rows) == 2

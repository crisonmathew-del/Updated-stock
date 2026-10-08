from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.jobs import JobAlreadyRunningError, job_lock
from app.core.redis import get_redis
from app.data import jobs
from app.data.backfill import run_backfill
from app.data.eod_update import run_eod_update
from app.data.universe import plan_universe, sync_tickers
from app.models import DailyBar, DataQualityIssue, JobRun, SharesOutstanding, Ticker
from app.providers.base import ActionKind, CorporateActionRecord, PriceHistory
from app.scheduler import eod_already_done
from tests.fakes import FakePrices, make_history
from tests.test_universe import listed

HISTORY_START = date(2025, 6, 2)
BACKFILLED_TO = date(2026, 9, 25)
SESSION = date(2026, 10, 2)


def histories(symbols: list[str], end: date, **overrides: PriceHistory) -> dict[str, PriceHistory]:
    data = {s: make_history(s, HISTORY_START, end) for s in symbols}
    data.update(overrides)
    return data


async def seeded_universe(db: AsyncSession) -> list[str]:
    plan = plan_universe(listed("AAPL", "A"))
    await sync_tickers(db, plan, BACKFILLED_TO)
    symbols = list(plan)
    await run_backfill(
        db,
        FakePrices(histories(symbols, BACKFILLED_TO)),
        get_redis(),
        today=BACKFILLED_TO,
        end=BACKFILLED_TO,
        years=2,
    )
    return symbols


async def open_issues(db: AsyncSession) -> dict[str, list[str]]:
    rows = (
        await db.scalars(select(DataQualityIssue).where(DataQualityIssue.resolved_at.is_(None)))
    ).all()
    issues: dict[str, list[str]] = {}
    for row in rows:
        issues.setdefault(row.severity, []).append(row.check)
    return issues


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_eod_update_brings_every_ticker_current(db: AsyncSession) -> None:
    symbols = await seeded_universe(db)
    aapl = await db.scalar(select(Ticker).where(Ticker.symbol == "AAPL"))
    assert aapl is not None
    db.add(
        SharesOutstanding(
            ticker_id=aapl.id,
            as_of_date=date(2026, 7, 1),
            filed_date=date(2026, 8, 1),
            shares=1_000,
            source="test",
        )
    )
    await db.commit()
    prices = FakePrices(histories(symbols, SESSION))

    result = await run_eod_update(db, prices, get_redis(), session_date=SESSION, backfill_years=2)

    assert result.tickers == len(symbols)
    assert result.missing == []
    assert result.new_splits == []
    # Only the last five sessions are requested (2026-09-28 … 2026-10-02).
    assert {(start, end) for _, start, end in prices.requests} == {(date(2026, 9, 28), SESSION)}
    ends = {t.bars_end for t in (await db.scalars(select(Ticker))).all()}
    assert ends == {SESSION}
    assert result.market_caps == 1
    await db.refresh(aapl)
    assert aapl.market_cap == pytest.approx(1_000 * aapl_close(prices))


def aapl_close(prices: FakePrices) -> float:
    return prices.histories["AAPL"].bars[-1].close


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_new_split_triggers_a_full_refetch(db: AsyncSession) -> None:
    symbols = await seeded_universe(db)
    split = CorporateActionRecord(date(2026, 9, 30), ActionKind.SPLIT, 2.0)
    adjusted = make_history(
        "AAPL", HISTORY_START, SESSION, first_close=50, step=0.25, actions=[split]
    )
    prices = FakePrices(histories(symbols, SESSION, AAPL=adjusted))

    result = await run_eod_update(db, prices, get_redis(), session_date=SESSION, backfill_years=2)

    assert result.new_splits == ["AAPL"]
    assert result.backfilled == 1
    full_refetch = [r for r in prices.requests if r[0] == ("AAPL",)]
    assert full_refetch[0][1] == date(2024, 10, 2)  # two years back from the session
    aapl = await db.scalar(select(Ticker).where(Ticker.symbol == "AAPL"))
    assert aapl is not None
    assert aapl.backfill_status == "done"
    first_close = await db.scalar(
        select(DailyBar.close).where(DailyBar.ticker_id == aapl.id, DailyBar.date == HISTORY_START)
    )
    assert first_close == 50  # old pre-split prices were replaced by adjusted ones


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_eod_job_records_its_run_and_satisfies_the_scheduler(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without an SEC contact (whatever the developer's .env says), data health warns.
    monkeypatch.setattr(get_settings(), "sec_user_agent", None)
    symbols = await seeded_universe(db)
    assert not await eod_already_done(SESSION.isoformat())

    stats = await jobs.eod_update_job(
        "manual", session_date=SESSION, prices=FakePrices(histories(symbols, SESSION))
    )

    run = (await db.scalars(select(JobRun).where(JobRun.job_name == "eod_update"))).one()
    assert run.status == "succeeded"
    assert stats["session"] == "2026-10-02"
    assert await eod_already_done(SESSION.isoformat())
    issues = await open_issues(db)
    assert "critical" not in issues, issues
    assert "sec_not_configured" in issues["warning"]


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_first_backfill_fills_in_market_caps(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh install loads the universe (with share counts), then backfills: market caps
    # must be there straight away, not only after the first nightly update.
    monkeypatch.setattr(jobs, "latest_session", lambda: BACKFILLED_TO)
    monkeypatch.setattr(jobs, "market_today", lambda: BACKFILLED_TO)
    plan = plan_universe(listed("AAPL", "A"))
    await sync_tickers(db, plan, BACKFILLED_TO)
    aapl = await db.scalar(select(Ticker).where(Ticker.symbol == "AAPL"))
    assert aapl is not None
    db.add(
        SharesOutstanding(
            ticker_id=aapl.id,
            as_of_date=date(2026, 6, 30),
            filed_date=date(2026, 8, 1),
            shares=1_000_000,
            source="test",
        )
    )
    await db.commit()

    stats = await jobs.backfill_job(
        "manual", years=1, prices=FakePrices(histories(list(plan), BACKFILLED_TO))
    )

    assert stats["market_caps"] == 1  # A has no share count
    close = await db.scalar(
        select(DailyBar.close).where(DailyBar.ticker_id == aapl.id, DailyBar.date == BACKFILLED_TO)
    )
    await db.refresh(aapl)
    assert close is not None
    assert aapl.market_cap == pytest.approx(close * 1_000_000)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_job_that_finds_the_lock_taken_is_recorded_and_does_nothing(
    db: AsyncSession,
) -> None:
    async with job_lock(get_redis(), jobs.INGEST_LOCK):
        with pytest.raises(JobAlreadyRunningError):
            await jobs.backfill_job("manual", prices=FakePrices({}))

    run = (await db.scalars(select(JobRun))).one()
    assert (run.job_name, run.status) == ("backfill", "failed")
    assert run.error == "JobAlreadyRunningError: ingest is already running"


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_an_empty_universe_is_critical(db: AsyncSession) -> None:
    await jobs.data_quality_job("manual", session_date=SESSION)

    assert "universe_empty" in (await open_issues(db))["critical"]


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_missed_eod_update_is_critical(db: AsyncSession) -> None:
    await seeded_universe(db)  # bars only up to 2026-09-25

    await jobs.data_quality_job("manual", session_date=SESSION)

    critical = (await open_issues(db))["critical"]
    assert "dataset_stale" in critical
    assert "universe_stale" in critical

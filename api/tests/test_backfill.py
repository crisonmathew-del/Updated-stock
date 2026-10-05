from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.data.backfill import backfill_start, read_progress, run_backfill
from app.data.bars import upsert_actions, upsert_bars
from app.data.universe import BENCHMARKS, plan_universe, sync_tickers
from app.models import CorporateAction, DailyBar, Ticker
from app.providers.base import ActionKind, CorporateActionRecord, RateLimitedError
from tests.fakes import FakePrices, make_history
from tests.test_universe import listed

TODAY = date(2026, 10, 2)
START = backfill_start(TODAY, 2)  # 2024-10-02


async def ticker_ids(db: AsyncSession) -> dict[str, int]:
    return {t.symbol: t.id for t in (await db.scalars(select(Ticker))).all()}


async def bar_count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(DailyBar)) or 0)


@pytest.mark.integration
async def test_bar_upserts_are_idempotent_and_pick_up_revisions(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL")), TODAY)
    aapl = (await ticker_ids(db))["AAPL"]
    history = make_history("AAPL", date(2026, 9, 1), date(2026, 9, 30))

    assert await upsert_bars(db, {aapl: history}, "test") == 21
    await db.commit()
    assert await upsert_bars(db, {aapl: history}, "test") == 0

    revised = make_history("AAPL", date(2026, 9, 29), date(2026, 9, 30), first_close=999)
    assert await upsert_bars(db, {aapl: revised}, "test") == 2
    await db.commit()
    assert await bar_count(db) == 21
    close = await db.scalar(select(DailyBar.close).where(DailyBar.date == date(2026, 9, 30)))
    assert close == 999.5


@pytest.mark.integration
async def test_only_new_splits_are_reported(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL")), TODAY)
    aapl = (await ticker_ids(db))["AAPL"]
    actions = [
        CorporateActionRecord(date(2024, 6, 10), ActionKind.SPLIT, 4.0),
        CorporateActionRecord(date(2024, 8, 12), ActionKind.DIVIDEND, 0.25),
    ]
    history = make_history("AAPL", date(2024, 6, 3), date(2024, 8, 30), actions=actions)

    assert await upsert_actions(db, {aapl: history}, "test") == [(aapl, date(2024, 6, 10), 4.0)]
    assert await upsert_actions(db, {aapl: history}, "test") == []
    await db.commit()
    kinds = (await db.scalars(select(CorporateAction.kind).order_by(CorporateAction.ex_date))).all()
    assert kinds == ["split", "dividend"]


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_backfill_records_each_outcome_and_publishes_progress(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL", "A", "TSM", "SPOT")), TODAY)
    prices = FakePrices(
        histories={
            "AAPL": make_history("AAPL", date(2020, 1, 2), TODAY),
            "SPY": make_history("SPY", date(2020, 1, 2), TODAY),
            "SPOT": make_history("SPOT", date(2025, 3, 3), TODAY),  # listed inside the window
        },
        errors={"A": "no data (possibly delisted or not on Yahoo)", "TSM": "HTTPError: boom"},
    )

    progress = await run_backfill(
        db, prices, get_redis(), today=TODAY, end=TODAY, years=2, symbols=None, batch_size=4
    )

    benchmark_symbols = {b.symbol for b in BENCHMARKS}
    assert set(prices.requests[0][0]) <= benchmark_symbols  # benchmarks go first
    assert all(start == START for _, start, _ in prices.requests)
    tickers = {t.symbol: t for t in (await db.scalars(select(Ticker))).all()}
    assert tickers["AAPL"].backfill_status == "done"
    assert (tickers["AAPL"].bars_start, tickers["AAPL"].bars_end) == (date(2024, 10, 2), TODAY)
    assert tickers["AAPL"].listed_date is None
    assert tickers["SPOT"].listed_date == date(2025, 3, 3)
    assert (tickers["A"].backfill_status, tickers["A"].backfill_error) == (
        "no_data",
        "no data (possibly delisted or not on Yahoo)",
    )
    assert (tickers["TSM"].backfill_status, tickers["TSM"].backfill_error) == (
        "failed",
        "HTTPError: boom",
    )
    # The 15 other benchmarks have no fake bars, so like A they count as "no data".
    assert (progress.done, progress.no_data, progress.failed) == (3, 16, 1)
    assert progress.total == 20
    published = await read_progress(get_redis())
    assert (published.status, published.done, published.processed) == ("succeeded", 3, 20)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_backfill_resumes_failed_and_pending_tickers_only(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL", "TSM")), TODAY)
    history = {"AAPL": make_history("AAPL", START, TODAY)}
    await run_backfill(
        db,
        FakePrices(history, {"TSM": "HTTPError: boom"}),
        get_redis(),
        today=TODAY,
        end=TODAY,
        years=2,
        symbols=["AAPL", "TSM"],
    )

    retry = FakePrices({"TSM": make_history("TSM", START, TODAY)})
    progress = await run_backfill(
        db, retry, get_redis(), today=TODAY, end=TODAY, years=2, symbols=["AAPL", "TSM"]
    )

    assert retry.requests == [(("TSM",), START, TODAY)]
    assert progress.done == 1
    forced = FakePrices(history)
    await run_backfill(
        db, forced, get_redis(), today=TODAY, end=TODAY, years=2, symbols=["AAPL"], force=True
    )
    assert forced.requests == [(("AAPL",), START, TODAY)]


class ThrottledPrices(FakePrices):
    async def daily_history(self, symbols, start, end):  # type: ignore[no-untyped-def]
        if "TSM" in symbols:
            raise RateLimitedError("Yahoo is still rate-limiting after backing off")
        return await super().daily_history(symbols, start, end)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_rate_limit_abort_keeps_completed_batches(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL", "TSM")), TODAY)
    prices = ThrottledPrices({"AAPL": make_history("AAPL", START, TODAY)})

    with pytest.raises(RateLimitedError):
        await run_backfill(
            db,
            prices,
            get_redis(),
            today=TODAY,
            end=TODAY,
            years=2,
            symbols=["AAPL", "TSM"],
            batch_size=1,
        )

    statuses = dict((await db.execute(select(Ticker.symbol, Ticker.backfill_status))).all())
    assert statuses["AAPL"] == "done"
    assert statuses["TSM"] == "pending"
    published = await read_progress(get_redis())
    assert published.status == "failed"
    assert published.message is not None
    assert "run the backfill again to resume" in published.message


def test_backfill_start_handles_leap_days() -> None:
    assert backfill_start(date(2028, 2, 29), 10) == date(2018, 2, 28)
    assert backfill_start(date(2026, 10, 2), 10) == date(2016, 10, 2)

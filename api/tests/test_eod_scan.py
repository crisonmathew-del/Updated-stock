"""The analytics pipeline against a small synthetic market in the test database."""

import math
from datetime import date

import polars as pl
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_back, sessions_between
from app.core.redis import get_redis
from app.data.backfill import run_backfill
from app.data.bars import upsert_bars
from app.data.loaders import read_frame
from app.data.universe import plan_universe, sync_tickers
from app.models import Ticker
from app.providers.base import PriceHistory
from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.fakes import FakePrices, make_history
from tests.test_universe import listed

END = date(2026, 10, 2)
START = sessions_back(END, 330)
SETTINGS = AppSettings()
STOCKS = {  # symbol: (first close, daily step, SIC code, SIC title)
    "AAPL": (100, 0.40, "3571", "Electronic Computers"),
    "A": (50, 0.05, "3826", "Laboratory Analytical Instruments"),
    "SPOT": (80, 0.30, "7372", "Services-Prepackaged Software"),
    "TSM": (60, 0.25, "3674", "Semiconductors & Related Devices"),
    "BAM": (40, -0.02, "6282", "Investment Advice"),
    "SEB": (300, -0.30, "2041", "Grain Mill Products"),
    "O": (55, 0.01, "6798", "Real Estate Investment Trusts"),
    "EPD": (25, 0.02, "4922", "Natural Gas Transmission"),
}


async def seed_market(db: AsyncSession, through: date = END) -> FakePrices:
    plan = plan_universe(listed(*STOCKS))
    await sync_tickers(db, plan, START)
    histories: dict[str, PriceHistory] = {}
    for symbol in plan:
        first, step = (STOCKS[symbol][0], STOCKS[symbol][1]) if symbol in STOCKS else (400, 0.2)
        histories[symbol] = make_history(symbol, START, through, first_close=first, step=step)
    prices = FakePrices(histories)
    await run_backfill(db, prices, get_redis(), today=through, end=through, years=2)
    for symbol, (_, _, sic, title) in STOCKS.items():
        await db.execute(
            update(Ticker)
            .where(Ticker.symbol == symbol)
            .values(sic_code=sic, sic_description=title)
        )
    await db.commit()
    return prices


async def snapshot(through: date | None = None) -> dict[str, pl.DataFrame]:
    cutoff = f"WHERE date <= '{through}'" if through else ""
    return {
        "indicators": await read_frame(
            f"SELECT * FROM indicators_daily {cutoff} ORDER BY ticker_id, date"
        ),
        "breadth": await read_frame(f"SELECT * FROM market_breadth_daily {cutoff} ORDER BY date"),
        "groups": await read_frame(
            f"SELECT group_id, date, rank, score, members, median_rs, tt_passing, new_highs "
            f"FROM group_rank_history {cutoff} ORDER BY date, group_id"
        ),
        "regime": await read_frame(
            "SELECT date, index_symbol, state, distribution_days, rally_day, is_ftd "
            f"FROM market_regime_daily {cutoff} ORDER BY date, index_symbol"
        ),
    }


def assert_same(a: pl.DataFrame, b: pl.DataFrame, name: str) -> None:
    assert a.columns == b.columns, name
    assert a.height == b.height, name
    for column in a.columns:
        for x, y in zip(a[column].to_list(), b[column].to_list(), strict=True):
            if isinstance(x, float) and isinstance(y, float):
                assert math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12), (name, column, x, y)
            else:
                assert x == y, (name, column, x, y)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_full_rebuild_fills_every_table(db: AsyncSession) -> None:
    await seed_market(db)

    result = await run_analytics(db, SETTINGS, through=END)

    assert result.mode == "full"
    assert result.groups == 8  # each stock's SIC code is its own 2-digit group (min 5 members)
    tables = await snapshot()
    last = tables["indicators"].filter(pl.col("date") == END)
    tickers = {t.id: t for t in (await db.scalars(select(Ticker))).all()}
    by_symbol = {tickers[r["ticker_id"]].symbol: r for r in last.iter_rows(named=True)}
    stocks = {s: r for s, r in by_symbol.items() if s in STOCKS}
    best = max(stocks, key=lambda s: stocks[s]["rs_raw"])
    worst = min(stocks, key=lambda s: stocks[s]["rs_raw"])
    assert (stocks[best]["rs_rating"], stocks[worst]["rs_rating"]) == (99, 1)
    assert worst == "SEB"  # the only steep decliner
    assert by_symbol["SPY"]["rs_rating"] is None  # funds and indexes aren't ranked
    assert by_symbol["SPY"]["rs_raw"] is not None
    assert by_symbol["AAPL"]["stage"] == 2
    assert all(not t.indicators_stale for t in tickers.values())
    assert tickers[next(i for i, t in tickers.items() if t.symbol == "O")].sector == "Real Estate"
    assert tables["breadth"].height == len(sessions_between(START, END))  # one row per session
    assert set(tables["regime"]["index_symbol"].unique()) == {"SPY", "QQQ", "IWM", "MARKET"}
    assert result.market_state == "Confirmed uptrend"


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_incremental_updates_match_a_full_rebuild(db: AsyncSession) -> None:
    await seed_market(db)
    earlier = sessions_back(END, 3)

    first = await run_analytics(db, SETTINGS, through=earlier)
    second = await run_analytics(db, SETTINGS, through=END)

    assert (first.mode, second.mode) == ("full", "incremental")
    assert second.new_dates == sessions_between(sessions_back(END, 2), END)
    incremental = await snapshot()
    await run_analytics(db, SETTINGS, through=END, force_full=True)
    rebuilt = await snapshot()
    for name in incremental:
        assert_same(incremental[name], rebuilt[name], name)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_future_bars_never_change_past_results(db: AsyncSession) -> None:
    """Spec §12 lookahead guard: same output with or without later bars in the database."""
    await seed_market(db)
    cutoff = sessions_back(END, 5)

    await run_analytics(db, SETTINGS, through=cutoff, force_full=True)
    with_future = await snapshot(cutoff)
    await db.execute(text("DELETE FROM daily_bars WHERE date > :d"), {"d": cutoff})
    await db.commit()
    await run_analytics(db, SETTINGS, through=cutoff, force_full=True)
    without_future = await snapshot(cutoff)

    for name in with_future:
        assert_same(with_future[name], without_future[name], name)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_rewritten_history_is_recomputed(db: AsyncSession) -> None:
    prices = await seed_market(db)
    await run_analytics(db, SETTINGS, through=END)
    aapl = await db.scalar(select(Ticker).where(Ticker.symbol == "AAPL"))
    assert aapl is not None
    before = await read_frame(
        f"SELECT date, sma50 FROM indicators_daily WHERE ticker_id = {aapl.id}"
    )

    # A 2-for-1 split re-fetch rewrites every bar at half the price.
    halved = PriceHistory(
        "AAPL",
        [
            b.__class__(b.date, b.open / 2, b.high / 2, b.low / 2, b.close / 2, b.volume * 2)
            for b in prices.histories["AAPL"].bars
        ],
    )
    await upsert_bars(db, {aapl.id: halved}, "test")
    aapl.indicators_stale = True
    await db.commit()

    result = await run_analytics(db, SETTINGS, through=END)

    assert result.recomputed == ["AAPL"]
    after = await read_frame(
        f"SELECT date, sma50 FROM indicators_daily WHERE ticker_id = {aapl.id}"
    )
    joined = before.join(after, on="date", suffix="_after").drop_nulls()
    assert all(math.isclose(a / 2, b) for a, b in joined.select("sma50", "sma50_after").iter_rows())
    await db.refresh(aapl)
    assert aapl.indicators_stale is False


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_regime_follows_settings(db: AsyncSession) -> None:
    await seed_market(db)
    await run_analytics(db, SETTINGS, through=END)
    small_caps = AppSettings(small_cap_mode=True)

    await run_analytics(db, small_caps, through=END)

    market = await read_frame(
        f"SELECT reasons FROM market_regime_daily WHERE index_symbol = 'MARKET' AND date = '{END}'"
    )
    reasons = str(market["reasons"][0])  # JSONB arrives as text
    assert "IWM: " in reasons
    assert "SPY: " in reasons

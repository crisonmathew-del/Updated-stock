"""Point-in-time liquidity filter: as-traded price, dollar volume and market cap on a date."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.universe import plan_universe, sync_tickers
from app.models import CorporateAction, DailyBar, IndicatorDaily, SharesOutstanding, Ticker
from app.scanner.universe_filter import liquid_tickers, liquidity_on
from app.settings.schema import AppSettings
from tests.test_universe import listed

DAY = date(2025, 3, 3)


@pytest.mark.integration
async def test_filters_use_as_traded_prices_and_point_in_time_market_cap(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL", "GOOGL", "TSM", "A")), DAY)
    ids = {t.symbol: t.id for t in (await db.scalars(select(Ticker))).all()}
    rows = {  # adjusted close, 50-day dollar volume
        "AAPL": (8.0, 50e6),  # 2-for-1 split later: traded at $16 that day
        "GOOGL": (8.0, 50e6),  # no split: an $8 stock
        "TSM": (150.0, 900e6),  # ADR: no share count
        "A": (120.0, 5e6),  # too little dollar volume
    }
    for symbol, (close, dollars) in rows.items():
        tid = ids[symbol]
        db.add(
            DailyBar(
                ticker_id=tid,
                date=DAY,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1,
                source="test",
            )
        )
        db.add(IndicatorDaily(ticker_id=tid, date=DAY, avg_dollar_volume_50=dollars))
    db.add(
        CorporateAction(
            ticker_id=ids["AAPL"], ex_date=date(2025, 6, 2), kind="split", value=2.0, source="test"
        )
    )
    # Shares counted at 2024-12-31 (filed in time), and a later filing that isn't public yet.
    db.add(
        SharesOutstanding(
            ticker_id=ids["AAPL"],
            as_of_date=date(2024, 12, 31),
            filed_date=date(2025, 2, 1),
            shares=100_000_000,
            form="10-K",
            source="test",
        )
    )
    db.add(
        SharesOutstanding(
            ticker_id=ids["AAPL"],
            as_of_date=date(2025, 3, 31),
            filed_date=date(2025, 4, 30),
            shares=1,
            form="10-Q",
            source="test",
        )
    )
    await db.commit()

    found = {s.ticker_id: s for s in await liquidity_on(db, DAY)}
    aapl = found[ids["AAPL"]]
    assert aapl.price == 16.0
    # $8 adjusted × 100M shares × 2 (the split after the count) = $1.6B.
    assert aapl.market_cap == pytest.approx(1.6e9)
    assert found[ids["TSM"]].market_cap is None

    settings = AppSettings()
    assert await liquid_tickers(db, settings, DAY) == {ids["AAPL"], ids["TSM"]}
    no_adrs = AppSettings(include_adrs=False)
    assert await liquid_tickers(db, no_adrs, DAY) == {ids["AAPL"]}
    # Small-cap mode ($5, $5M, $300M): the $8 stock and the $5M-a-day stock pass too.
    small_caps = AppSettings(small_cap_mode=True)
    assert await liquid_tickers(db, small_caps, DAY) == set(ids.values()) - _benchmarks(ids)


def _benchmarks(ids: dict[str, int]) -> set[int]:
    return {tid for symbol, tid in ids.items() if symbol not in ("AAPL", "GOOGL", "TSM", "A")}

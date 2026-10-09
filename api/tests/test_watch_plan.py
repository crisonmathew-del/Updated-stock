"""What the streamer watches: holdings first, then the market indexes, setups by readiness,
rule targets and watchlists, capped; each with the levels its rules need from the latest
session."""

from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.bars import upsert_bars
from app.data.universe import plan_universe, sync_tickers
from app.intraday.plan import load_plan
from app.intraday.store import ticker_ids
from app.models import (
    AlertRule,
    Holding,
    IndicatorDaily,
    Setup,
    User,
    Watchlist,
    WatchlistItem,
)
from app.providers.base import Bar, PriceHistory
from app.settings.schema import AppSettings
from tests.test_universe import listed

AS_OF = date(2026, 10, 1)
TODAY = date(2026, 10, 2)
SYMBOLS = ("AAPL", "SPOT", "TSM", "BRK.B", "EPD")


def setup_row(ticker_id: int, state: str, readiness: float, **values: Any) -> Setup:
    base: dict[str, Any] = {
        "ticker_id": ticker_id,
        "kind": "base",
        "pattern_type": "vcp",
        "state": state,
        "state_since": AS_OF,
        "first_seen": AS_OF,
        "as_of": AS_OF,
        "close": 100.0,
        "pivot": 101.0,
        "readiness_pct": readiness,
        "score": 80.0,
        "raw_score": 80.0,
        "grade": "A",
        "regime_multiplier": 1.0,
        "penalties": 0.0,
        "components": [],
        "red_flags": [],
        "trade_plan": {"entry": 101.1, "stop": 95.0, "shares": 160, "buy_zone": [101.0, 106.05]},
    }
    base.update(values)
    return Setup(**base)


@pytest.mark.integration
async def test_the_plan_orders_caps_and_carries_levels(db: AsyncSession, user: User) -> None:
    await sync_tickers(db, plan_universe(listed(*SYMBOLS)), AS_OF)
    ids = await ticker_ids(db, SYMBOLS)
    await upsert_bars(
        db,
        {tid: PriceHistory(s, [Bar(AS_OF, 99, 101, 98, 100.0, 1_000)]) for s, tid in ids.items()},
        "test",
    )
    for tid in ids.values():
        db.add(IndicatorDaily(ticker_id=tid, date=AS_OF, avg_volume_50=800_000.0, sma50=97.5))
    db.add_all(
        [
            setup_row(ids["SPOT"], "near_pivot", 1.5),
            setup_row(ids["TSM"], "near_pivot", 0.4),
            setup_row(ids["BRK.B"], "basing", 6.0),
            Holding(
                user_id=user.id,
                ticker_id=ids["EPD"],
                opened_on=AS_OF,
                entry_price=30.0,
                shares=100,
                initial_stop=28.0,
                stop=28.5,
            ),
        ]
    )
    watchlist = Watchlist(user_id=user.id, name="Ideas")
    db.add(watchlist)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=watchlist.id, ticker_id=ids["AAPL"]))
    db.add(
        AlertRule(
            user_id=user.id,
            name="Loses the 50-day",
            scope="watchlist",
            watchlist_id=watchlist.id,
            condition="ma_cross_below",
            ma="sma50",
            channels=["in_app"],
        )
    )
    await db.commit()

    plan = await load_plan(db, AppSettings(), TODAY)
    assert plan.as_of == AS_OF
    assert list(plan.reasons.items()) == [
        ("EPD", "holding"),
        ("SPY", "market index"),  # for the top bar and the market card, never scanned
        ("QQQ", "market index"),
        ("IWM", "market index"),
        ("TSM", "near pivot"),  # 0.4% from its pivot: closer than SPOT
        ("SPOT", "near pivot"),
        ("AAPL", "alert rule"),
        ("BRK.B", "basing"),
    ]
    tsm = plan.contexts["TSM"]
    assert tsm.setup is not None
    assert (tsm.setup.pivot, tsm.setup.buy_zone_top, tsm.setup.shares) == (101.0, 106.05, 160)
    assert (tsm.prev_close, tsm.avg_volume) == (100.0, 800_000.0)
    epd = plan.contexts["EPD"]
    assert [(h.entry, h.stop) for h in epd.holdings] == [(30.0, 28.5)]
    [rule] = plan.contexts["AAPL"].rules
    assert (rule.condition, rule.level, rule.channels) == ("ma_cross_below", 97.5, ("in_app",))
    assert set(plan.universe) == set(SYMBOLS)
    assert plan.universe["SPOT"].grade == "A"

    capped = await load_plan(db, AppSettings(stream_max_symbols=2), TODAY)
    assert capped.symbols == ["EPD", "SPY"]
    assert set(capped.universe) == set(SYMBOLS)  # the scans still cover everything

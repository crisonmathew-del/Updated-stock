"""Minute bars in the database and the learned volume curve."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import MARKET_TZ, sessions_between
from app.data.bars import upsert_bars
from app.data.universe import plan_universe, sync_tickers
from app.intraday.store import (
    current_curve,
    learn_volume_curve,
    load_bars,
    prev_closes,
    save_bars,
    ticker_ids,
)
from app.intraday.volume import STANDARD_CURVE
from app.providers.base import Bar, MinuteBar, PriceHistory
from app.settings.schema import AppSettings
from tests.test_universe import listed

DAY = date(2026, 10, 2)


def minute(symbol: str, day: date, hh: int, mm: int, volume: int = 100) -> MinuteBar:
    ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=MARKET_TZ)
    return MinuteBar(symbol, ts, 10.0, 10.5, 9.5, 10.2, volume)


@pytest.mark.integration
async def test_minute_bars_round_trip_and_previous_closes(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT", "AAPL")), DAY)
    ids = await ticker_ids(db, ["spot", "aapl", "nope"])
    assert set(ids) == {"SPOT", "AAPL"}
    bars = [minute("SPOT", DAY, 9, 31), minute("SPOT", DAY, 9, 30), minute("AAPL", DAY, 8, 0)]
    assert await save_bars(db, bars, ids, "replay") == 3
    # Saving the same minute again keeps the first.
    await save_bars(db, [minute("SPOT", DAY, 9, 30, volume=999)], ids, "alpaca")
    loaded = await load_bars(db, DAY)
    assert [(b.symbol, b.ts.astimezone(MARKET_TZ).strftime("%H:%M"), b.volume) for b in loaded] == [
        ("AAPL", "08:00", 100),
        ("SPOT", "09:30", 100),
        ("SPOT", "09:31", 100),
    ]
    assert [b.symbol for b in await load_bars(db, DAY, ["SPOT"])] == ["SPOT", "SPOT"]
    assert await load_bars(db, DAY + timedelta(days=1)) == []

    previous = DAY - timedelta(days=1)
    history = PriceHistory("SPOT", [Bar(previous, 90, 93, 89, 92.75, 1_000)])
    await upsert_bars(db, {ids["SPOT"]: history}, "test")
    await db.commit()
    assert await prev_closes(db, DAY, ["SPOT", "AAPL"]) == {"SPOT": 92.75}


@pytest.mark.integration
async def test_volume_curve_is_learned_once_enough_sessions_are_stored(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT")), DAY)
    ids = await ticker_ids(db, ["SPOT"])
    settings = AppSettings(volume_curve_min_sessions=2)
    assert await current_curve(db, settings) is STANDARD_CURVE
    days = sessions_between(date(2026, 9, 28), DAY)[-2:]
    # One full session first: not enough to learn from.
    first = [minute("SPOT", days[0], 9 + (30 + m) // 60, (30 + m) % 60) for m in range(390)]
    await save_bars(db, first, ids, "test")
    assert await learn_volume_curve(db, settings, DAY) is None
    second = [minute("SPOT", days[1], 9 + (30 + m) // 60, (30 + m) % 60) for m in range(390)]
    await save_bars(db, second, ids, "test")
    learned = await learn_volume_curve(db, settings, DAY)
    assert learned is not None
    # Flat volume: a straight line to the pre-auction share.
    assert learned.fraction(195) == pytest.approx(0.46)
    current = await current_curve(db, settings)
    assert current.source == "learned from 2 sessions"
    assert current.fraction(195) == pytest.approx(0.46)
    # A stricter setting falls back to the standard curve.
    assert await current_curve(db, AppSettings(volume_curve_min_sessions=3)) is STANDARD_CURVE

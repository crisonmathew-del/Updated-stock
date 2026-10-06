"""Building a day from prints, and replaying recorded minute bars as a feed."""

from datetime import date, datetime
from pathlib import Path

import pytest

from app.core.calendar import MARKET_TZ
from app.intraday.day import DayState
from app.providers.base import MinuteBar, Trade
from app.providers.replay import (
    ReplayStream,
    format_recording,
    parse_recording,
    read_recording,
    ticks_from_bar,
    write_recording,
)

DAY = date(2026, 10, 2)


def et(hh: int, mm: int, ss: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=MARKET_TZ)


def bar(
    symbol: str, hh: int, mm: int, o: float, h: float, low: float, c: float, v: int
) -> MinuteBar:
    return MinuteBar(symbol, et(hh, mm), o, h, low, c, v)


def test_day_state_separates_premarket_from_the_session() -> None:
    day = DayState("SPOT")
    assert day.add(Trade("SPOT", et(8, 0, 5), 95.0, 1_000)) == []
    assert (day.premarket_volume, day.volume, day.open) == (1_000, 0, None)
    # The first regular print sets the open and closes the pre-market minute bar.
    done = day.add(Trade("SPOT", et(9, 30, 1), 93.0, 500))
    assert [(b.ts, b.open, b.close, b.volume) for b in done] == [(et(8, 0), 95.0, 95.0, 1_000)]
    day.add(Trade("SPOT", et(9, 30, 20), 93.5, 200))
    day.add(Trade("SPOT", et(9, 30, 40), 92.8, 300))
    assert (day.open, day.high, day.low, day.last, day.previous) == (93.0, 93.5, 92.8, 92.8, 93.5)
    assert day.volume == 1_000
    # A late print counts towards volume but doesn't move prices.
    day.add(Trade("SPOT", et(9, 30, 30), 99.0, 50))
    assert (day.high, day.last, day.volume) == (93.5, 92.8, 1_050)
    done = day.add(Trade("SPOT", et(9, 31, 0), 93.1, 100))
    assert [(b.open, b.high, b.low, b.close, b.volume) for b in done] == [
        (93.0, 93.5, 92.8, 92.8, 1_050)
    ]
    # After the close nothing moves.
    assert day.add(Trade("SPOT", et(16, 5), 80.0, 10_000)) == []
    assert (day.last, day.volume) == (93.1, 1_150)


def test_a_new_session_starts_a_new_day() -> None:
    day = DayState("SPOT")
    day.add(Trade("SPOT", et(15, 59, 30), 93.0, 100))
    done = day.add(Trade("SPOT", et(9, 30, 0, day=date(2026, 10, 5)), 94.0, 10))
    assert [b.ts for b in done] == [et(15, 59)]
    assert (day.session, day.open, day.volume, day.previous) == (date(2026, 10, 5), 94.0, 10, None)


@pytest.mark.parametrize(
    ("o", "h", "low", "c", "order"),
    [
        (10.0, 12.0, 9.0, 11.5, [10.0, 9.0, 12.0, 11.5]),  # up bar: low first
        (10.0, 12.0, 9.0, 9.5, [10.0, 12.0, 9.0, 9.5]),  # down bar: high first
    ],
)
def test_replayed_prints_rebuild_the_bar(
    o: float, h: float, low: float, c: float, order: list[float]
) -> None:
    source = bar("SPOT", 10, 0, o, h, low, c, 1_003)
    ticks = ticks_from_bar(source)
    assert [t.price for t in ticks] == order
    assert [t.size for t in ticks] == [250, 250, 250, 253]
    assert [t.timestamp.second for t in ticks] == [0, 15, 30, 45]
    day = DayState("SPOT")
    for t in ticks:
        day.add(t)
    assert day.flush() == source


def test_recordings_round_trip(tmp_path: Path) -> None:
    bars = [bar("SPOT", 9, 31, 1, 2, 0.5, 1.5, 10), bar("AAPL", 9, 30, 5, 6, 4, 5.5, 20)]
    text = format_recording(bars)
    assert text.splitlines()[0] == "symbol,ts,open,high,low,close,volume"
    assert parse_recording(text) == sorted(bars, key=lambda b: (b.ts, b.symbol))
    path = tmp_path / "session.csv.gz"
    write_recording(bars, path)
    assert read_recording(path) == parse_recording(text)
    with pytest.raises(ValueError, match="missing column"):
        parse_recording("symbol,ts\nSPOT,2026-10-02T09:30:00-04:00\n")
    with pytest.raises(ValueError, match="no UTC offset"):
        parse_recording(
            "symbol,ts,open,high,low,close,volume\nSPOT,2026-10-02T09:30:00,1,1,1,1,1\n"
        )


async def test_replay_streams_subscribed_names_and_snapshots_all() -> None:
    bars = [
        bar("SPOT", 9, 30, 92.0, 92.5, 91.9, 92.4, 4_000),
        bar("AAPL", 9, 30, 230.0, 231.0, 229.5, 230.5, 8_000),
        bar("SPOT", 9, 31, 92.4, 93.0, 92.3, 92.9, 4_000),
    ]
    feed = ReplayStream(bars, speed=0, prev_closes={"SPOT": 91.0, "AAPL": 229.0})
    await feed.subscribe(["spot"])
    seen = [(t.symbol, t.price) async for t in feed.trades()]
    assert {s for s, _ in seen} == {"SPOT"}
    assert [p for _, p in seen] == [92.0, 91.9, 92.5, 92.4, 92.4, 92.3, 93.0, 92.9]
    assert feed.finished.is_set()
    assert feed.now() == et(9, 31, 45)
    snaps = await feed.snapshots(["SPOT", "AAPL", "NOPE"])
    assert set(snaps) == {"SPOT", "AAPL"}
    aapl = snaps["AAPL"]
    assert (aapl.last, aapl.prev_close, aapl.volume, aapl.high) == (230.5, 229.0, 8_000, 231.0)


async def test_replay_skips_quickly_to_the_start_time() -> None:
    bars = [bar("SPOT", 8, 0, 1, 1, 1, 1, 4), bar("SPOT", 9, 30, 2, 2, 2, 2, 4)]
    # One real second per replayed second would take 90 minutes; `start` skips the gap.
    feed = ReplayStream(bars, speed=1, start="09:30")
    await feed.subscribe(["SPOT"])
    count = 0
    async for _ in feed.trades():
        count += 1
        if count == 5:  # the first print at or after 09:30
            break
    assert feed.now() == et(9, 30)

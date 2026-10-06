"""The intraday watcher's rules on hand-worked numbers.

SPOT: pivot 92.46, buy zone up to 97.08, plan entry 92.56 / stop 88.71 / 259 shares; average
volume 1,000,000; previous close 92.00. At 10:15 (45 minutes in) the standard curve says 18% of
a day's volume has traded, so 360,000 shares project to 2,000,000: 200% of average (strong).
"""

from datetime import date, datetime

import pytest

from app.core.calendar import MARKET_TZ
from app.intraday.day import DayState
from app.intraday.volume import STANDARD_CURVE
from app.scanner.intraday_scan import (
    HoldingWatch,
    RuleWatch,
    SetupWatch,
    WatchContext,
    evaluate,
)
from app.settings.schema import AppSettings

DAY = date(2026, 10, 2)
SETTINGS = AppSettings()
SETUP = SetupWatch(
    setup_id=5,
    state="near_pivot",
    pattern="VCP",
    pivot=92.46,
    buy_zone_top=97.08,
    entry=92.56,
    stop=88.71,
    shares=259,
    score=84.2,
    grade="A",
)


def et(hh: int, mm: int) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hh, mm, tzinfo=MARKET_TZ)


def ctx(**changes: object) -> WatchContext:
    base: dict[str, object] = {
        "symbol": "SPOT",
        "ticker_id": 1,
        "name": "Spotify",
        "prev_close": 92.0,
        "avg_volume": 1_000_000.0,
        "setup": SETUP,
    }
    base.update(changes)
    return WatchContext(**base)  # type: ignore[arg-type]


def day(
    price: float, at: datetime, volume: int = 360_000, previous: float | None = None
) -> DayState:
    return DayState(
        "SPOT",
        DAY,
        open=92.1,
        high=max(price, 92.1),
        low=min(price, 92.1),
        last=price,
        last_ts=at,
        previous=previous,
        volume=volume,
    )


def run(context: WatchContext, state: DayState, fired: set[str] | None = None, **kw: object):  # type: ignore[no-untyped-def]
    return evaluate(
        context,
        state,
        curve=STANDARD_CURVE,
        settings=SETTINGS,
        fired=set() if fired is None else fired,
        **kw,  # type: ignore[arg-type]
    )


def test_breakout_on_projected_volume_fires_once() -> None:
    fired: set[str] = set()
    events = run(ctx(), day(92.80, et(10, 15)), fired)
    assert [(e.kind, e.priority) for e in events] == [("breakout_provisional", "high")]
    e = events[0]
    assert e.title == "SPOT breaking out strongly (provisional)"
    assert (
        "92.80 is above the 92.46 pivot on projected volume of 200% of average (strong)" in e.body
    )
    assert "entry 92.56, stop 88.71, 259 shares" in e.body
    assert (e.data["projected_volume"], e.data["volume_ratio_pct"]) == (2_000_000, 200)
    assert e.data["strong"] is True
    assert run(ctx(), day(93.0, et(10, 16)), fired) == []


def test_no_breakout_on_light_volume_or_too_early() -> None:
    # 180,000 by 10:15 projects to 1,000,000: 100% of average, under 140%.
    assert run(ctx(), day(92.80, et(10, 15), volume=180_000)) == []
    # 09:32 is inside the first 5 minutes, before projections count.
    assert run(ctx(), day(92.80, et(9, 32), volume=360_000)) == []
    # Below the pivot nothing happens however heavy the volume.
    assert run(ctx(), day(92.40, et(10, 15), volume=900_000)) == []


def test_partial_feed_volume_is_scaled_and_said_so() -> None:
    # 9,000 IEX shares at a 2.5% share = 360,000 market-wide: 200%.
    events = run(
        ctx(), day(92.80, et(10, 15), volume=9_000), volume_share=0.025, partial_volume=True
    )
    assert [e.kind for e in events] == ["breakout_provisional"]
    assert "IEX volume scaled to the whole market" in events[0].body
    assert events[0].data["partial_volume"] is True


def test_past_the_buy_zone_is_extended_not_a_breakout() -> None:
    events = run(ctx(), day(98.00, et(10, 15)))
    assert [(e.kind, e.priority) for e in events] == [("breakout_extended", "normal")]
    # 98.00 / 92.46 - 1 = 5.99%.
    assert (
        "98.00 is 6.0% above the 92.46 pivot, beyond the buy zone (up to 97.08)" in events[0].body
    )


def test_a_broken_out_setup_that_hits_its_stop() -> None:
    broken = ctx(setup=SetupWatch(**{**SETUP.__dict__, "state": "breakout"}))
    events = run(broken, day(88.70, et(11, 0)))
    assert [(e.kind, e.priority, e.title) for e in events] == [
        ("setup_stop", "high", "SPOT hit its stop")
    ]
    # A setup that never broke out has no stop to hit.
    assert run(ctx(), day(88.70, et(11, 0))) == []


def test_a_holding_at_its_stop() -> None:
    holding = HoldingWatch(9, 1, entry=92.56, stop=88.71, initial_stop=88.71, shares=259)
    events = run(ctx(setup=None, holdings=(holding,)), day(88.50, et(11, 0)))
    assert [(e.kind, e.priority) for e in events] == [("holding_stop", "high")]
    # (88.50 - 92.56) / (92.56 - 88.71) = -4.06 / 3.85 = -1.05R; -4.06 × 259 = -1,051.54.
    assert "88.50 is at or below your stop 88.71 (-1.1R, -1,052)" in events[0].body
    assert events[0].data["r"] == pytest.approx((88.50 - 92.56) / 3.85)


def test_rules_fire_on_crossings_once() -> None:
    above = RuleWatch(1, 1, "Over 95", "price_above", 95, 95, None, "high", ("in_app",))
    below_ma = RuleWatch(
        2, 1, "Loses the 50-day", "ma_cross_below", None, 90, "sma50", "normal", ()
    )
    context = ctx(setup=None, rules=(above, below_ma))
    fired: set[str] = set()
    assert run(context, day(94.90, et(10, 0), previous=94.80), fired) == []
    events = run(context, day(95.10, et(10, 1), previous=94.90), fired)
    assert [(e.kind, e.priority, e.title, e.body) for e in events] == [
        ("rule", "high", "SPOT: Over 95", "Crossed above 95.00 (95.00): now 95.10.")
    ]
    assert run(context, day(95.20, et(10, 2), previous=95.10), fired) == []
    events = run(context, day(89.90, et(11, 0), previous=90.20), fired)
    assert [e.body for e in events] == ["Crossed below the 50-day SMA (90.00): now 89.90."]
    # At the first print, the previous close is the reference: opening above 95 is a cross.
    first = run(ctx(setup=None, rules=(above,), prev_close=94.0), day(95.5, et(9, 30)))
    assert [e.kind for e in first] == ["rule"]


def test_change_and_volume_rules() -> None:
    up = RuleWatch(3, 1, "Up 5%", "change_above", 5, None, None, "normal", ())
    down = RuleWatch(4, 1, "Down 4%", "change_below", -4, None, None, "normal", ())
    heavy = RuleWatch(5, 1, "Heavy volume", "volume_ratio_above", 1.5, None, None, "normal", ())
    context = ctx(setup=None, rules=(up, down, heavy))
    # 96.70 / 92.00 - 1 = 5.11%; projected 2.0× average.
    bodies = [e.body for e in run(context, day(96.70, et(10, 15)))]
    assert bodies == [
        "Up 5.1% on the day (your threshold +5.0%).",
        "Projected volume 2.0× the 50-day average (your threshold 1.5×).",
    ]
    bodies = [e.body for e in run(context, day(88.0, et(10, 15), volume=0))]
    assert bodies == ["Down 4.3% on the day (your threshold -4.0%)."]


def test_premarket_prints_trigger_nothing() -> None:
    assert run(ctx(), day(95.0, et(8, 30))) == []

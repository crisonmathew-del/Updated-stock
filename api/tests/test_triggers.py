"""Pullback and undercut-and-rally triggers: a textbook case each, then one near-miss per rule
(default settings: RS ≥ 85, breakout within 30 sessions, touch within 1% of the average,
undercut by at most 3% within the last 5 sessions)."""

from dataclasses import replace

import numpy as np

from app.patterns.bars import Bars
from app.scoring.triggers import Trigger, pullback_to_average, undercut_and_rally
from app.settings.schema import DEFAULTS
from tests.pattern_fixtures import plain_bars, sessions

# --- Pullback to a rising average -----------------------------------------------------------
# Sessions 0-19 at 100, the breakout closes at 102 on session 20, a straight run to 110 on
# session 26 (high 111), a light-volume drift down to 105 on session 31, then session 32 dips
# to 104.5 (the 10-day EMA is 104: 104 × 1.01 = 105.04 ≥ 104.5) and closes up at 106.
T = 32
BREAKOUT = 20


def pullback_bars(**overrides: float) -> Bars:
    close = [100.0] * 20 + list(np.linspace(102, 110, 7)) + list(np.linspace(109, 105, 5))
    close.append(overrides.get("close", 106.0))
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    low[T] = overrides.get("low", 104.5)
    volume = [1000.0] * 27 + [overrides.get("pullback_volume", 700.0)] * 5 + [1200.0]
    bars = plain_bars(high, low, close, volume=volume, avg_volume=1000.0)
    return replace(
        bars,
        ema10=np.full(T + 1, overrides.get("ema10", 104.0)),
        ema21=np.full(T + 1, 101.0),
        sma50=np.full(T + 1, overrides.get("sma50", 98.0)),
    )


def pullback(bars: Bars, rs: int | None = 92, breakout: int | None = BREAKOUT) -> Trigger | None:
    return pullback_to_average(bars, DEFAULTS, rs_rating=rs, breakout_index=breakout)


def test_textbook_pullback_to_the_10_day() -> None:
    assert pullback(pullback_bars()) == Trigger(
        "pullback",
        "Pulled back from 111.00 on 70% of average volume to the 10-day EMA (104.00) and "
        "closed up at 106.00, 12 sessions after the breakout.",
        104.0,
    )


def test_pullback_near_misses() -> None:
    bars = pullback_bars()
    assert pullback(bars, rs=84) is None  # not a strong enough leader
    assert pullback(bars, rs=None) is None
    assert pullback(bars, breakout=None) is None
    assert pullback(bars, breakout=1) is None  # 31 sessions ago: too long
    assert pullback(bars, breakout=T - 1) is None  # yesterday: no pullback yet
    assert pullback(pullback_bars(close=104.9)) is None  # down on the day (105 → 104.9)
    assert pullback(pullback_bars(pullback_volume=1100.0)) is None  # heavy pullback
    assert pullback(pullback_bars(low=105.1)) is None  # 1.06% above the 10-day EMA
    assert pullback(pullback_bars(ema10=106.5)) is None  # closed below the average


def test_the_50_day_counts_only_on_its_first_test() -> None:
    # No EMAs; the 50-day at 100 and today's low 100.5. Post-breakout lows were ≥ 102.33.
    bars = replace(
        pullback_bars(low=100.5, sma50=100.0),
        ema10=np.full(T + 1, np.nan),
        ema21=np.full(T + 1, np.nan),
    )
    first = pullback(bars)
    assert first is not None
    assert (first.level, "to the 50-day SMA (100.00)" in first.detail) == (100.0, True)
    low = bars.low.copy()
    low[24] = 100.8  # an earlier dip to the 50-day after the breakout
    assert pullback(replace(bars, low=low)) is None


# --- Undercut & rally -----------------------------------------------------------------------
# A base at 100 with a swing low of 95 on session 10. Sessions 24-28 slide to a low of 93.5
# (1.6% under 95), session 28 closes at 94.8, then session 29 closes back above 95 at 96.5
# on 120% of average volume.
DAYS = sessions(30)


def undercut_bars(**overrides: float) -> Bars:
    close = [100.0] * 24 + [98.0, 96.0, 94.5, 94.6, overrides.get("yesterday", 94.8), 96.5]
    close[10] = 96.0
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    low[26] = overrides.get("undercut_low", 93.5)
    volume = [1000.0] * 29 + [overrides.get("volume", 1200.0)]
    return plain_bars(high, low, close, volume=volume, avg_volume=1000.0)


def undercut(bars: Bars, lows: list[tuple[int, float]] | None = None) -> Trigger | None:
    levels = [(DAYS[i], price) for i, price in ([(10, 95.0)] if lows is None else lows)]
    return undercut_and_rally(bars, DEFAULTS, lows=levels)


def test_textbook_undercut_and_rally() -> None:
    assert undercut(undercut_bars()) == Trigger(
        "undercut_rally",
        f"Undercut the base low of {DAYS[10].isoformat()} (95.00) by 1.6% and closed back "
        "above it at 96.50 on 120% of average volume.",
        95.0,
    )


def test_the_nearest_qualifying_low_wins() -> None:
    # 98 on session 15 was undercut by 4.6% (too deep), so 95 on session 10 is the level.
    result = undercut(undercut_bars(), [(10, 95.0), (15, 98.0)])
    assert result is not None
    assert result.level == 95.0


def test_undercut_near_misses() -> None:
    assert undercut(undercut_bars(), [(25, 95.0)]) is None  # the level is inside the window
    assert undercut(undercut_bars(undercut_low=91.0)) is None  # 4.2% under: too deep
    assert undercut(undercut_bars(), [(10, 93.0)]) is None  # lows stayed above 93: no undercut
    assert undercut(undercut_bars(volume=900.0)) is None  # below-average volume
    assert undercut(undercut_bars(yesterday=95.5)) is None  # already back above yesterday
    assert undercut(undercut_bars(), []) is None

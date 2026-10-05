"""Swing detection and the shared building blocks, checked by hand, plus Hypothesis
properties (no lookahead, alternation, scale invariance)."""

from datetime import date
from itertools import pairwise

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.patterns.bars import weekly
from app.patterns.context import (
    base_number,
    breakout_status,
    prior_uptrend,
    tightness,
    volume_dry_up,
)
from app.patterns.swings import HIGH, LOW, Swing, zigzag
from app.patterns.types import Status
from app.settings.schema import DEFAULTS
from tests.pattern_fixtures import chart, plain_bars

HIGHS = np.array([10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 12], dtype=float)
LOWS = HIGHS - 1


def test_zigzag_by_hand() -> None:
    # Threshold 2. Bar 1: range 11 - 9 = 2 and the low came first → low 9 (bar 0), confirmed
    # on bar 1. Rises to 12 (bar 2); bar 3's low 10 is 2 below → high confirmed on bar 3.
    # Falls to 8 (bar 5); bar 6's high 10 is 2 above → low confirmed on bar 6. Rises to 13
    # (bar 9); bar 10's low 11 is 2 below → high confirmed on bar 10, now tracking low 11.
    swings, pending = zigzag(HIGHS, LOWS, np.full(len(HIGHS), 2.0))
    assert swings == [
        Swing(0, 9.0, LOW, 1),
        Swing(2, 12.0, HIGH, 3),
        Swing(5, 8.0, LOW, 6),
        Swing(9, 13.0, HIGH, 10),
    ]
    assert pending == Swing(10, 11.0, LOW, -1)


def test_zigzag_waits_for_a_known_threshold() -> None:
    threshold = np.full(len(HIGHS), 2.0)
    threshold[:4] = np.nan  # e.g. ATR not available yet
    swings, _ = zigzag(HIGHS, LOWS, threshold)
    # Nothing can confirm before bar 4. Then the range so far is 12 (bar 2) - 9 (bar 0) = 3,
    # and the low came first: low 9 at bar 0, confirmed on bar 4.
    assert swings[0] == Swing(0, 9.0, LOW, 4)
    assert [s.index for s in swings] == [0, 2, 5, 9]


walks = st.lists(st.floats(-3, 3, allow_nan=False), min_size=5, max_size=120)


@given(walks, st.floats(0.5, 4))
@settings(max_examples=150, deadline=None)
def test_zigzag_properties(steps: list[float], limit: float) -> None:
    close = 100 + np.cumsum(steps)
    high, low = close + 0.5, close - 0.5
    threshold = np.full(len(close), limit)
    swings, pending = zigzag(high, low, threshold)

    # Alternating kinds, each at the bar's actual extreme, confirmed after it happened.
    assert all(a.kind != b.kind for a, b in pairwise(swings))
    for s in swings:
        assert s.price == (high[s.index] if s.kind == HIGH else low[s.index])
        assert s.index < s.confirmed_at
    # No lookahead: a prefix sees exactly the swings confirmed within it.
    for cut in range(1, len(close)):
        prefix, _ = zigzag(high[:cut], low[:cut], threshold[:cut])
        assert prefix == [s for s in swings if s.confirmed_at < cut]
    # Scale invariance: prices and threshold × 8 (exact in binary floating point, so a
    # comparison sitting exactly on the threshold can't flip) give the same swing bars.
    scaled, _ = zigzag(high * 8, low * 8, threshold * 8)
    assert [(s.index, s.kind, s.confirmed_at) for s in scaled] == [
        (s.index, s.kind, s.confirmed_at) for s in swings
    ]
    assert pending is None or pending.confirmed_at == -1


def test_prior_uptrend_measures_from_the_lowest_low_before_the_base() -> None:
    bars = chart([(0, 50), (100, 100), (130, 90)])
    trend = prior_uptrend(bars, 100, DEFAULTS)
    assert trend is not None
    # Lookback 130 sessions reaches bar 0: low 50 × 0.995 = 49.75; high 100 × 1.005 = 100.5.
    assert trend.low_index == 0
    assert trend.gain_pct == pytest.approx((100.5 / 49.75 - 1) * 100)
    assert prior_uptrend(bars, 0, DEFAULTS) is None


def test_breakout_status() -> None:
    closes = [95, 96, 97, 98, 99, 101, 102]
    bars = plain_bars([c + 1 for c in closes], [c - 1 for c in closes], closes)
    recent = breakout_status(bars, pivot=100, after=1)
    assert recent is not None
    assert (recent.status, recent.index, recent.end) == (Status.BROKEN_OUT, 5, 4)
    forming = breakout_status(bars, pivot=110, after=1)
    assert forming is not None
    assert (forming.status, forming.index, forming.end) == (Status.FORMING, None, 6)
    old = plain_bars([102] * 3 + [96] * 4, [100] * 3 + [94] * 4, [101] * 3 + [95] * 4)
    # Closed above 100 on bar 2 (4 sessions before the last): old news, not a current base.
    assert breakout_status(old, pivot=100, after=1) is None


def test_volume_dry_up_by_hand() -> None:
    volume = [1000.0] * 10 + [400, 600, 900, 300, 1000, 450, 800, 700, 500, 350]
    bars = plain_bars([10] * 20, [9] * 20, volume=volume, avg_volume=1000)
    dry = volume_dry_up(bars, 19, DEFAULTS)
    assert dry is not None
    # Last 10 average 600 → 0.6 of 1000; below 500 (50%): 400, 300, 450, 350 → 4 days.
    assert dry.ratio == pytest.approx(0.6)
    assert dry.quiet_days == 4


def test_tightness_counts_narrow_and_inside_days() -> None:
    high = [20, 20, 20, 20, 20, 20, 20, 20, 19.5, 19.4, 19.6, 19.3, 19.2]
    low = [10, 10, 10, 10, 10, 10, 10, 10, 18.0, 18.5, 18.2, 18.6, 18.8]
    bars = plain_bars(high, low)
    tight = tightness(bars, 12)
    # Last 5 bars (8-12) ranges 1.5, 0.9, 1.4, 0.7, 0.4: NR7 on 8, 9, 11, 12;
    # bar 10 (19.6/18.2) is neither NR7 nor inside bar 9 → 4 narrow days.
    assert tight.narrow_days == 4
    assert tight.atr_ratio is None


def test_base_number_counts_stage_2_bases_and_resets_on_an_undercut() -> None:
    # Stage 2 throughout. Highs: run to 100 (bar 9), 30-session base down to 88 (12%),
    # new high 110 (bar 40), base down to 99 (10%), new high 120 (bar 71) = current base start.
    high = (
        list(np.linspace(80, 100, 10))
        + [92] * 15
        + [89] * 15  # bars 10-39, lows 88
        + [110]
        + [103] * 15
        + [100] * 15  # bars 41-70, lows 99
        + [120]
    )
    low = [h - 1 for h in high]
    bars = plain_bars(high, low)
    assert base_number(bars, 71, base_low=105, settings=DEFAULTS) == 3
    # The current base undercuts the previous base's low (99): count resets to 1.
    assert base_number(bars, 71, base_low=95, settings=DEFAULTS) == 1
    # Too-shallow pullbacks (under 8%) are not bases.
    shallow = plain_bars([100.0, *([97.0] * 30), 101.0], [99.0, *([96.0] * 30), 100.0])
    assert base_number(shallow, 31, base_low=95, settings=DEFAULTS) == 1
    # Stage 3/4: not a Stage 2 base. Stage 1: a first base.
    assert base_number(plain_bars(high, low, stage=4), 71, 105, DEFAULTS) is None
    assert base_number(plain_bars(high, low, stage=1), 71, 105, DEFAULTS) == 1


def test_weekly_bars_follow_iso_weeks() -> None:
    bars = chart([(0, 100), (12, 112)])  # 2019-01-02 (Wed) .. 2019-01-18 (Fri)
    weeks = weekly(bars, last_week_complete=True)
    assert weeks.dates == [date(2019, 1, 4), date(2019, 1, 11), date(2019, 1, 18)]
    assert list(weeks.first) == [0, 3, 8]
    assert list(weeks.last) == [2, 7, 12]
    assert weeks.close[1] == pytest.approx(107.0)
    assert weeks.high[1] == pytest.approx(bars.high[3:8].max())
    assert weeks.volume[0] == pytest.approx(3_000_000)
    assert list(weekly(bars, last_week_complete=False).complete) == [True, True, False]

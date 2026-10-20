"""The breakout watch's readiness score and early entry, worked by hand with the default
settings (weights: distance 25, tightness 20, dry-up 15, RS 15, demand 15, support 10)."""

from dataclasses import replace

import numpy as np
import pytest

from app.patterns.bars import Bars
from app.scoring.breakout_watch import early_entry, watch_score
from app.settings.schema import DEFAULTS
from tests.pattern_fixtures import plain_bars


def tight_base(*, atr_ratio: float = 0.7, last_close: float = 19.6) -> Bars:
    """12 sessions: 7 wide ones, then 5 tight ones (high 19.80, low 19.30) closing 70% up the
    day's range, the last at `last_close`; 600k shares a day lately vs a 1M 50-day average."""
    highs = [21.0] * 7 + [19.8] * 5
    lows = [18.0] * 7 + [19.3] * 5
    closes = [19.0] * 7 + [19.65] * 4 + [last_close]
    volume = [1_400_000.0] * 2 + [600_000.0] * 10
    bars = plain_bars(highs, lows, closes, volume=volume, avg_volume=1_000_000.0)
    return replace(bars, atr_ratio_10_50=np.full(len(highs), atr_ratio))


def score(bars: Bars, **overrides: object) -> float:
    kwargs: dict[str, object] = {
        "pivot": 20.0,
        "rs_new_high_ahead": False,
        "rs_line_high_52w": True,
        "rs_slope_63": 0.1,
        "up_down_volume": 1.25,
        "pocket_pivots_recent": 1,
    }
    kwargs.update(overrides)
    return watch_score(bars, DEFAULTS, **kwargs).score  # type: ignore[arg-type]


def test_a_tight_quiet_base_just_under_its_pivot_scores_by_hand() -> None:
    result = watch_score(
        tight_base(),
        DEFAULTS,
        pivot=20.0,
        rs_new_high_ahead=False,
        rs_line_high_52w=True,
        rs_slope_63=0.1,
        up_down_volume=1.25,
        pocket_pivots_recent=1,
    )
    parts = {p.key: p for p in result.parts}
    # Distance: (20 - 19.6) / 19.6 = 2.04% below → (8 - 2.04) / 8 = 0.745 × 25 = 18.6.
    assert parts["distance"].points == 18.6
    assert parts["distance"].detail == "2.0% below the pivot 20.00."
    # Tightness: ATR ratio 0.70 → (1.0 - 0.7) / (1.0 - 0.6) = 0.75 × 20 = 15.
    assert parts["tightness"].points == 15.0
    # Dry-up: 600k / 1M = 0.60 → full 15.
    assert parts["dry_up"].points == 15.0
    # RS line at a 52-week high (not ahead of price): 0.8 × 15 = 12.
    assert parts["relative_strength"].points == 12.0
    # Demand: up/down 1.25 → 0.5, plus 0.4 for a recent pocket pivot = 0.9 × 15 = 13.5.
    assert parts["demand"].points == 13.5
    assert parts["demand"].detail.endswith("1 pocket pivot in the last 10 days.")
    # Support: closes 70, 70, 70, 70 and 60% up the range → 68% ≥ 60% → full 10.
    assert parts["support"].points == 10.0
    assert result.score == 84.1  # 18.62 + 15 + 15 + 12 + 13.5 + 10
    assert sum(p.max_points for p in result.parts) == 100


def test_the_early_entry_is_the_top_of_the_tight_area() -> None:
    entry = early_entry(tight_base(), DEFAULTS, pivot=20.0)
    assert entry is not None
    # High 19.80 + $0.10 = 19.90 (below the 20.10 pivot entry); low 19.30 × 0.999 = 19.2807
    # → 19.28; risk 0.62 = 3.12%. $1,000 ÷ 0.62 = 1,612 shares, capped at 25% ÷ 19.90 = 1,256.
    assert (entry["entry"], entry["stop"], entry["risk_pct"]) == (19.9, 19.28, 3.12)
    assert entry["shares"] == 1256
    assert entry["spread_pct"] == 2.53  # (19.80 - 19.30) / 19.80
    assert "the base may never break out" in entry["text"]


def test_no_early_entry_when_the_area_is_loose_or_already_at_the_pivot() -> None:
    assert early_entry(tight_base(), DEFAULTS, pivot=19.95) is None  # 19.80 only 0.75% below
    loose = replace(
        tight_base(), high=np.full(12, 21.0), low=np.full(12, 18.0)
    )  # 14% from high to low
    assert early_entry(loose, DEFAULTS, pivot=22.0) is None


def test_parts_without_data_are_left_out_and_the_rest_scaled_up() -> None:
    bars = tight_base(atr_ratio=float("nan"))
    result = watch_score(
        bars,
        DEFAULTS,
        pivot=20.0,
        rs_new_high_ahead=False,
        rs_line_high_52w=True,
        rs_slope_63=0.1,
        up_down_volume=None,
        pocket_pivots_recent=0,
    )
    parts = {p.key: p for p in result.parts}
    assert parts["tightness"].max_points == 0
    assert parts["demand"].max_points == 0
    # Distance 25, dry-up 15, RS 15, support 10 = 65 points → × 100/65.
    assert sum(p.max_points for p in result.parts) == pytest.approx(100, abs=0.2)
    expected = (18.6224 + 15 + 12 + 10) * 100 / 65
    assert result.score == pytest.approx(expected, abs=0.1)


def test_distance_is_full_at_the_pivot_and_nothing_past_the_buy_zone() -> None:
    at = score(tight_base(last_close=20.3))  # 1.5% above the pivot, inside the 5% zone
    past = score(tight_base(last_close=21.2))  # 5.7% above (of the close): past the zone
    assert at > past
    bars = tight_base(last_close=21.2)
    parts = {
        p.key: p
        for p in watch_score(
            bars,
            DEFAULTS,
            pivot=20.0,
            rs_new_high_ahead=False,
            rs_line_high_52w=False,
            rs_slope_63=None,
            up_down_volume=1.0,
            pocket_pivots_recent=0,
        ).parts
    }
    assert parts["distance"].points == 0
    assert parts["distance"].detail == "5.7% above the pivot: past the buy zone."


def test_the_score_only_reads_bars_up_to_the_as_of_session() -> None:
    bars = tight_base()
    later = plain_bars(
        list(bars.high) + [25.0] * 5,
        list(bars.low) + [15.0] * 5,
        list(bars.close) + [24.0] * 5,
        volume=list(bars.volume) + [9_000_000.0] * 5,
        avg_volume=1_000_000.0,
    )
    later = replace(later, atr_ratio_10_50=np.full(len(later), 0.7))
    cut = later.until(bars.dates[-1])
    assert score(cut) == score(bars)

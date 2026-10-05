"""Setup Score and red flags, worked by hand with the default settings."""

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from app.scoring.red_flags import RedFlag, find_red_flags
from app.scoring.setup_score import ScoreInputs, readiness_pct, score_setup
from app.settings.schema import DEFAULTS
from tests.pattern_fixtures import plain_bars, sessions

LEADER = ScoreInputs(
    tt_passed=8,
    stage=2,
    rs_rating=94,
    rs_line_high_52w=True,
    rs_new_high_ahead=True,
    fundamentals_grade="A",
    pattern_quality=78.6,
    pattern_label="VCP",
    group_name="Semiconductors",
    group_rank=4,
    groups_ranked=152,
    up_down_volume=1.35,
    pocket_pivots_recent=1,
    insider_cluster=False,
    regime="confirmed_uptrend",
    regime_label="Confirmed uptrend",
)
# trend 20 (8/8 + Stage 2); RS 20 × (0.7 × 44/49 + 0.3) = 18.5714; fundamentals A 20;
# pattern 78.6/100 × 20 = 15.72; group rank 4 → 10; accumulation (0.5 + 0.3) × 10 = 8.
LEADER_RAW = 20 + 20 * (0.7 * 44 / 49 + 0.3) + 20 + 15.72 + 10 + 8


def points(result: object) -> dict[str, tuple[float, str]]:
    return {c.key: (round(c.points, 4), c.status) for c in result.components}  # type: ignore[attr-defined]


def test_a_leader_scores_every_component_by_hand() -> None:
    result = score_setup(LEADER, DEFAULTS)
    assert points(result) == {
        "trend": (20.0, "pass"),
        "relative_strength": (round(20 * (0.7 * 44 / 49 + 0.3), 4), "partial"),
        "fundamentals": (20.0, "pass"),
        "pattern": (15.72, "partial"),
        "group": (10.0, "pass"),
        "accumulation": (8.0, "partial"),
    }
    assert result.raw == round(LEADER_RAW, 2) == 92.29
    assert (result.multiplier, result.penalties, result.final) == (1.0, 0, 92.29)
    assert (result.grade, result.coverage_pct) == ("A+", 100.0)
    details = {c.key: c.detail for c in result.components}
    assert details["relative_strength"] == (
        "RS Rating 94; RS line at a 52-week high ahead of price."
    )
    assert details["accumulation"] == (
        "Up/down volume 1.35 (accumulation at 1.2); 1 pocket pivot(s) in the last 10 "
        "sessions; no insider cluster buy."
    )


def test_regime_multiplier_and_penalties_set_the_final_grade() -> None:
    pressure = score_setup(replace(LEADER, regime="uptrend_under_pressure"), DEFAULTS)
    assert (pressure.multiplier, pressure.final, pressure.grade) == (0.8, 73.83, "B")
    extended = RedFlag("extended", "Extended", 10, "Close 7.0% above the pivot.")
    flagged = score_setup(LEADER, DEFAULTS, [extended])
    assert (flagged.penalties, flagged.final, flagged.grade) == (10, 82.29, "A")
    correction = score_setup(replace(LEADER, regime="correction"), DEFAULTS, [extended])
    assert correction.final == pytest.approx(92.29 * 0.5 - 10, abs=0.01)
    assert correction.grade is None  # 36.1: not a recommendation


def test_missing_fundamentals_are_left_out_and_the_rest_scaled_up() -> None:
    result = score_setup(replace(LEADER, fundamentals_grade=None), DEFAULTS)
    assert result.coverage_pct == 80.0
    assert result.raw == pytest.approx((LEADER_RAW - 20) / 80 * 100, abs=0.01)
    fundamentals = next(c for c in result.components if c.key == "fundamentals")
    assert fundamentals.status == "no_data"
    assert fundamentals.detail.startswith("Fundamentals unknown")


def test_partial_credit_rules() -> None:
    weaker = replace(
        LEADER,
        tt_passed=6,
        stage=3,
        rs_rating=70,
        rs_line_high_52w=False,
        rs_slope_63=0.04,
        fundamentals_grade="C",
        pattern_quality=None,
        group_rank=100,
        up_down_volume=1.1,
        pocket_pivots_recent=0,
        insider_cluster=True,
    )
    result = score_setup(weaker, DEFAULTS)
    assert points(result) == {
        "trend": (12.0, "partial"),  # 20 × 0.8 × 6/8, no Stage 2 part
        "relative_strength": (round(20 * (0.7 * 20 / 49 + 0.15), 4), "partial"),
        "fundamentals": (12.0, "partial"),  # C = 60%
        "pattern": (0.0, "fail"),  # no base: zero, not missing
        "group": (3.25, "partial"),  # 10 × 0.7 × (152 - 100) / (152 - 40)
        "accumulation": (4.5, "partial"),  # (0.25 + 0.2) × 10
    }


def test_readiness_is_the_distance_to_the_pivot() -> None:
    assert readiness_pct(90.0, 92.46) == 2.73  # (92.46 - 90) / 90
    assert readiness_pct(95.0, 92.46) == -2.67
    assert readiness_pct(95.0, None) is None


# --- Red flags ------------------------------------------------------------------------------


def flags_by_key(flags: list[RedFlag]) -> dict[str, RedFlag]:
    return {f.key: f for f in flags}


def test_extended_climax_late_stage_and_earnings_flags() -> None:
    # 15 quiet sessions at 70, then 15 rising to 130; 50-day SMA set to 100.
    closes = [70.0] * 15 + list(np.linspace(70, 130, 15))
    bars = plain_bars([c + 1 for c in closes], [c - 1 for c in closes], closes)
    bars = replace(bars, sma50=np.full(30, 100.0))
    flags = flags_by_key(
        find_red_flags(
            bars,
            DEFAULTS,
            pivot=120.0,
            base_number=4,
            next_earnings=date(2026, 11, 3),
            sessions_to_earnings=3,
        )
    )
    assert flags["extended"].detail == (
        "Close 30.0% above the 50-day SMA (limit 25%); close 8.3% above the pivot (the buy "
        "zone ends at 5%): don't chase."
    )
    assert flags["extended"].penalty == 10
    # Lowest low of the last 15 sessions: 70 - 1 = 69, so up 88% (130 / 69).
    assert flags["climax"].detail.startswith("Up 88% from the low of the last 3 weeks")
    assert flags["late_stage"].detail == "Base 4 since Stage 2 began (late-stage from base 4)."
    assert (flags["earnings_soon"].penalty, flags["earnings_soon"].label) == (0, "Earnings risk")


def test_no_flags_for_a_quiet_stock_near_its_pivot() -> None:
    closes = [100.0 + (i % 3) * 0.5 for i in range(30)]
    bars = replace(
        plain_bars([c + 1 for c in closes], [c - 1 for c in closes], closes),
        sma50=np.full(30, 98.0),
    )
    assert find_red_flags(bars, DEFAULTS, pivot=101.5, base_number=2, sessions_to_earnings=20) == []


def test_wide_and_loose_weeks_and_distribution_in_the_base() -> None:
    days = sessions(20)
    # Sessions 0-4: a quiet week. Week 2 (sessions 5-9 if the calendar groups them so): a
    # 20% range closing at the low. Heavy down days on sessions 11, 13 and 15.
    high = [101.0] * 20
    low = [99.0] * 20
    close = [100.0] * 20
    week = [i for i in range(20) if days[i].isocalendar()[:2] == days[7].isocalendar()[:2]]
    for i in week:
        high[i], low[i], close[i] = 110.0, 90.0, 92.0
    close[week[-1]] = 90.5  # closes in the lower third of 90-110
    volume = [1000.0] * 20
    for i in (11, 13, 15):
        close[i] = close[i - 1] - 1
        volume[i] = 1600.0
    bars = plain_bars(high, low, close, volume=volume, avg_volume=1000.0)
    flags = flags_by_key(find_red_flags(bars, DEFAULTS, base_start=days[2]))
    assert flags["wide_and_loose"].detail.startswith("1 week(s) in the base with a range over 15%")
    assert days[week[0]].isoformat() in flags["wide_and_loose"].detail
    assert flags["distribution"].detail.startswith("3 down days in the base on 1.5× average")

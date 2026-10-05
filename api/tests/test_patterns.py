"""Pattern detectors on synthetic charts: a textbook example and near-misses for each rule,
breakout status, and the no-lookahead and scale properties of the whole detection run.

Charts draw closes as straight lines between waypoints (session, price) with a 1% daily
range; highs are 0.5% above the bar body and lows 0.5% below, which is where the expected
depths below come from (e.g. a swing high at a close of 100 is 100.50)."""

from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest

from app.core.calendar import ends_week
from app.patterns.bars import Bars
from app.patterns.detect import detect_patterns
from app.patterns.types import PatternMatch, PatternType, Status
from app.settings.schema import DEFAULTS
from tests.pattern_fixtures import chart, sessions

UP = [(0, 40.0), (260, 100.0)]  # a year-long advance into the base (+150%)
VCP = [
    *UP,
    (275, 76.0),  # contraction 1: 100.5 → 75.62 = 24.8%
    (290, 96.0),
    (300, 83.5),  # contraction 2: 96.48 → 83.08 = 13.9% (0.56× the first)
    (310, 93.0),
    (316, 87.4),  # contraction 3: 93.47 → 86.96 = 7.0% (0.50×)
    (322, 92.0),
    (326, 89.25),  # contraction 4: 92.46 → 88.80 = 4.0% (0.57×)
    (332, 91.5),  # rising toward the pivot 92.46
]
VCP_VOLUME = [(0, 1.0), (260, 1.3), (290, 1.0), (310, 0.7), (322, 0.5)]
CUP = [*UP, (285, 80.0), (310, 74.0), (335, 82.0), (365, 98.0), (375, 91.0), (380, 93.0)]


def detect(bars: Bars, releases: Sequence[tuple[date, str]] = ()) -> list[PatternMatch]:
    return detect_patterns(
        bars, DEFAULTS, last_week_complete=ends_week(bars.dates[-1]), releases=releases
    )


def of_type(bars: Bars, kind: PatternType, **kw: Sequence[tuple[date, str]]) -> list[PatternMatch]:
    return [m for m in detect(bars, **kw) if m.type == kind]


def only(bars: Bars, kind: PatternType, **kw: Sequence[tuple[date, str]]) -> PatternMatch:
    [match] = of_type(bars, kind, **kw)
    return match


# --- VCP ------------------------------------------------------------------------------------


def test_vcp_textbook() -> None:
    bars = chart(VCP, volumes=VCP_VOLUME)
    vcp = only(bars, PatternType.VCP)
    days = sessions(333)
    assert [round(c.depth_pct, 1) for c in vcp.contractions] == [24.8, 13.9, 7.0, 4.0]
    assert [c.high.date for c in vcp.contractions] == [days[260], days[290], days[310], days[322]]
    assert vcp.pivot == pytest.approx(92.0 * 1.005)  # high of the final contraction
    assert vcp.base_low == pytest.approx(76.0 * 0.995)
    assert (vcp.start, vcp.end, vcp.status) == (days[260], days[332], Status.FORMING)
    assert vcp.duration_weeks == pytest.approx(72 / 5)
    assert vcp.base_number == 1
    assert vcp.details["prior_uptrend_pct"] > 25
    keys = [c.key for c in vcp.components]
    assert keys == [
        "depth",
        "progression",
        "volume_trend",
        "volume",
        "tightness",
        "position",
        "base_count",
        "rs_line",
    ]
    assert sum(c.max_points for c in vcp.components) == 100
    by_key = {c.key: c for c in vcp.components}
    assert by_key["depth"].points == 15  # final contraction 4.0% < 6%
    assert by_key["volume_trend"].points == 8  # final contraction volume 38% of the first's
    # The base's later highs (H1-H3) can't start a base of their own: H0 is higher.
    assert not of_type(bars, PatternType.FLAT_BASE)


def test_vcp_breakout_is_reported_then_ages_out() -> None:
    days = sessions(400)
    broke = only(chart([*VCP, (334, 94.0)], volumes=VCP_VOLUME), PatternType.VCP)
    # Closes 92.75 (session 333) and 94: the first close above 92.46 is session 333.
    assert broke.status == Status.BROKEN_OUT
    assert broke.details["breakout_date"] == days[333].isoformat()
    assert broke.end == days[332]
    # Four sessions after the breakout it is history, not a current base.
    assert not of_type(chart([*VCP, (337, 97.0)], volumes=VCP_VOLUME), PatternType.VCP)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({300: 79.0}, "second contraction 18% is more than 0.7× the first"),
        ({316: 82.0, 322: 92.0, 326: 82.5}, "final contraction deeper than 10%"),
        ({275: 60.0}, "first contraction deeper than 35%"),
    ],
)
def test_vcp_near_misses(change: dict[int, float], why: str) -> None:
    waypoints = [(i, change.get(i, p)) for i, p in VCP]
    assert not of_type(chart(waypoints, volumes=VCP_VOLUME), PatternType.VCP), why


# --- Flat base ------------------------------------------------------------------------------


FLAT = [*UP, (270, 92.0), (285, 97.0), (300, 91.0), (315, 98.0), (325, 95.0)]


def test_flat_base_textbook() -> None:
    bars = chart(FLAT)
    flat = only(bars, PatternType.FLAT_BASE)
    days = sessions(326)
    # 100.50 high → 91 × 0.995 = 90.545 low: 9.9% deep over 13 weeks.
    assert flat.depth_pct == pytest.approx((100.5 - 90.545) / 100.5 * 100)
    assert flat.pivot == pytest.approx(100.5)
    assert (flat.start, flat.status, flat.duration_weeks) == (days[260], Status.FORMING, 13.0)
    # Contractions 8.9% then 7.1% (0.8×): not a VCP.
    assert not of_type(bars, PatternType.VCP)


def test_flat_base_near_misses() -> None:
    too_deep = [*UP, (270, 92.0), (285, 97.0), (300, 82.0), (315, 98.0), (325, 95.0)]
    assert not of_type(chart(too_deep), PatternType.FLAT_BASE)  # 18% deep
    too_short = [*UP, (270, 94.0), (279, 97.0)]  # 19 sessions < 5 weeks
    assert not of_type(chart(too_short), PatternType.FLAT_BASE)


# --- Cup with handle ------------------------------------------------------------------------


def test_cup_with_handle_textbook() -> None:
    bars = chart(CUP, volumes=[(0, 1.0), (365, 0.6)])
    cup = only(bars, PatternType.CUP_WITH_HANDLE)
    days = sessions(381)
    assert (cup.start, cup.status, cup.timeframe) == (days[260], Status.FORMING, "weekly")
    # Lip 100.50, bottom 74 × 0.995 = 73.63: 26.7% deep.
    assert cup.depth_pct == pytest.approx((100.5 - 73.63) / 100.5 * 100)
    assert cup.pivot == pytest.approx(98.0 * 1.005)  # handle high = right-side peak
    # Handle 98.49 → 91 × 0.995 = 90.545: 8.1% over 15 sessions.
    assert cup.details["handle_depth_pct"] == pytest.approx(8.07, abs=0.01)
    assert cup.details["handle_sessions"] == 15
    assert cup.details["bottom_third_share_pct"] > 40
    assert [p.date for p in cup.swings] == [days[260], days[310], days[365], days[375]]
    assert cup.duration_weeks == 24


@pytest.mark.parametrize(
    ("waypoints", "why"),
    [
        ([*UP, (275, 74.0), (290, 98.0), (298, 91.0), (303, 93.0)], "V-shaped: 33% at the bottom"),
        ([*CUP[:-2], (375, 84.0), (380, 86.0)], "handle in the lower half (14.7% deep)"),
        ([*CUP[:-2], (368, 96.0), (370, 97.0)], "handle under a week"),
        (
            [*UP, (285, 70.0), (310, 60.0), (335, 70.0), (365, 98.0), (375, 91.0), (380, 93.0)],
            "40% deep cup",
        ),
    ],
)
def test_cup_near_misses(waypoints: list[tuple[int, float]], why: str) -> None:
    assert not of_type(chart(waypoints), PatternType.CUP_WITH_HANDLE), why


def test_cup_may_be_deeper_in_a_bear_market() -> None:
    deep = [*UP, (285, 75.0), (310, 63.0), (335, 75.0), (365, 98.0), (375, 91.0), (380, 93.0)]
    assert not of_type(chart(deep), PatternType.CUP_WITH_HANDLE)  # 37% > 33%
    days = sessions(381)
    correction = set(days[280:300])
    cup = only(chart(deep, corrections=correction), PatternType.CUP_WITH_HANDLE)
    assert cup.details["bear_market_depth"] is True


# --- High tight flag ------------------------------------------------------------------------


HTF = [(0, 30.0), (200, 40.0), (235, 82.0), (245, 70.0), (255, 76.0)]
HTF_VOLUME = [(0, 1.0), (200, 3.0), (235, 1.0)]


def test_high_tight_flag_textbook() -> None:
    flag = only(chart(HTF, volumes=HTF_VOLUME), PatternType.HIGH_TIGHT_FLAG)
    days = sessions(256)
    assert flag.pivot == pytest.approx(82.0 * 1.005)
    assert flag.details["pole_gain_pct"] > 100
    # Flag 82.41 → 70 × 0.995 = 69.65: 15.5% over 4 weeks on a third of the pole's volume.
    assert flag.depth_pct == pytest.approx((82.41 - 69.65) / 82.41 * 100)
    assert flag.details["flag_volume_vs_pole"] < 0.5
    assert flag.swings[1].date == days[235]


def test_high_tight_flag_near_misses() -> None:
    weak_pole = [(0, 30.0), (200, 40.0), (235, 64.0), (245, 55.0), (255, 59.0)]
    assert not of_type(chart(weak_pole, volumes=HTF_VOLUME), PatternType.HIGH_TIGHT_FLAG)
    heavy_flag = [(0, 1.0), (200, 3.0), (235, 4.0)]
    assert not of_type(chart(HTF, volumes=heavy_flag), PatternType.HIGH_TIGHT_FLAG)
    young = [(0, 30.0), (200, 40.0), (235, 82.0), (242, 72.0)]  # flag only 7 sessions old
    assert not of_type(chart(young, volumes=HTF_VOLUME), PatternType.HIGH_TIGHT_FLAG)


# --- Three weeks tight ----------------------------------------------------------------------


def week_end_after(index: int) -> int:
    days = sessions(index + 10)
    return next(i for i in range(index, index + 10) if ends_week(days[i]))


def test_three_weeks_tight_textbook() -> None:
    end = week_end_after(222)
    bars = chart(
        [(0, 40.0), (200, 100.0), (203, 101.0), (end, 101.4)], volumes=[(0, 1.0), (203, 0.7)]
    )
    tight = only(bars, PatternType.THREE_WEEKS_TIGHT)
    closes = tight.details["weekly_closes"]
    assert len(closes) >= 3
    assert max(closes) / min(closes) - 1 <= 0.015
    assert tight.status == Status.FORMING
    assert tight.end == bars.dates[-1]


def test_three_weeks_tight_needs_tight_closes_and_completed_weeks() -> None:
    end = week_end_after(222)
    loose = [(0, 40.0), (200, 100.0), (205, 103.0), (210, 100.0), (215, 103.0), (end, 100.0)]
    assert not of_type(chart(loose), PatternType.THREE_WEEKS_TIGHT)
    tight = chart([(0, 40.0), (200, 100.0), (203, 101.0), (end, 101.4)])
    # Same chart seen mid-week: the current week isn't complete, so the run is one week shorter.
    midweek = detect_patterns(tight.until(tight.dates[-3]), DEFAULTS, last_week_complete=False)
    full = only(tight, PatternType.THREE_WEEKS_TIGHT)
    short = [m for m in midweek if m.type == PatternType.THREE_WEEKS_TIGHT]
    assert not short or len(short[0].details["weekly_closes"]) < len(full.details["weekly_closes"])


# --- Ascending base -------------------------------------------------------------------------


ASCENDING = [
    (0, 40.0),
    (200, 80.0),
    (210, 70.0),  # 80.40 → 69.65: 13.4%
    (225, 88.0),
    (235, 77.0),
    (250, 95.0),
    (260, 83.0),
    (270, 90.0),
]


def test_ascending_base_textbook() -> None:
    base = only(chart(ASCENDING), PatternType.ASCENDING_BASE)
    days = sessions(271)
    assert [round(c.depth_pct, 1) for c in base.contractions] == [13.4, 13.4, 13.5]
    assert base.pivot == pytest.approx(95.0 * 1.005)  # the high before the third pullback
    assert (base.start, base.status, base.duration_weeks) == (days[200], Status.FORMING, 14.0)


def test_ascending_base_needs_higher_lows() -> None:
    lower_third_low = [(i, 75.0 if i == 260 else p) for i, p in ASCENDING]
    assert not of_type(chart(lower_third_low), PatternType.ASCENDING_BASE)


# --- Pocket pivot ---------------------------------------------------------------------------


POCKET = [(0, 40.0), (250, 90.0), (258, 87.0), (259, 89.0)]


def test_pocket_pivot_textbook() -> None:
    bars = chart(POCKET, volumes=[(0, 1.0), (250, 1.5)], volume_on={259: 2.5})
    pivot = only(bars, PatternType.POCKET_PIVOT)
    assert pivot.start == pivot.end == sessions(260)[259]
    assert pivot.details["volume_vs_down_max"] == pytest.approx(2.5 / 1.5, abs=0.01)
    assert pivot.pivot == pytest.approx(89.0)


def test_pocket_pivot_near_misses() -> None:
    quiet = chart(POCKET, volumes=[(0, 1.0), (250, 1.5)], volume_on={259: 1.4})
    assert not of_type(quiet, PatternType.POCKET_PIVOT)  # doesn't beat the down days
    # A steep run with small down days: the up day closes 6% above its 10-day SMA (105.7).
    extended = [
        (0, 40.0),
        (240, 80.0),
        (252, 104.0),
        (253, 103.0),
        (254, 106.0),
        (255, 105.0),
        (256, 108.0),
        (257, 107.0),
        (258, 110.0),
        (259, 112.0),
    ]
    far = chart(extended, volumes=[(0, 1.0), (240, 1.5)], volume_on={259: 2.5})
    assert not of_type(far, PatternType.POCKET_PIVOT)  # too far above its averages


# --- Earnings gap ---------------------------------------------------------------------------


GAP = [(0, 40.0), (250, 60.0), (251, 67.0), (256, 68.0)]


def test_earnings_gap_textbook() -> None:
    days = sessions(257)
    bars = chart(GAP, open_on={251: 66.0}, volume_on={251: 4.0})
    gap = only(bars, PatternType.EARNINGS_GAP, releases=[(days[250], "after_close")])
    assert gap.start == days[251]
    assert gap.details["gap_pct"] == pytest.approx(10.0)
    assert gap.details["volume_multiple"] == pytest.approx(4.0)
    assert gap.base_low == pytest.approx(66.0 * 0.995)  # the gap-day low
    assert gap.status == Status.FORMING
    assert {c.key: c.points for c in gap.components}["catalyst"] == 20


def test_earnings_gap_needs_size_volume_and_a_results_filing() -> None:
    days = sessions(257)
    release = [(days[250], "after_close")]
    bars = chart(GAP, open_on={251: 66.0}, volume_on={251: 4.0})
    assert not of_type(bars, PatternType.EARNINGS_GAP)  # no results filing
    assert not of_type(bars, PatternType.EARNINGS_GAP, releases=[(days[245], "after_close")])
    light = chart(GAP, open_on={251: 66.0}, volume_on={251: 2.0})
    assert not of_type(light, PatternType.EARNINGS_GAP, releases=release)
    small = chart(GAP, open_on={251: 63.0}, volume_on={251: 4.0})  # +5%
    assert not of_type(small, PatternType.EARNINGS_GAP, releases=release)
    faded = chart([*GAP[:-1], (253, 64.0)], open_on={251: 66.0}, volume_on={251: 4.0})
    failed = only(faded, PatternType.EARNINGS_GAP, releases=release)
    assert failed.status == Status.FAILED  # closed below the gap-day low


# --- Properties of the whole run ------------------------------------------------------------


CHARTS: dict[str, tuple[list[tuple[int, float]], dict[str, Any]]] = {
    "vcp": ([*VCP, (334, 94.0), (345, 99.0)], {"volumes": VCP_VOLUME}),
    "cup": (CUP, {"volumes": [(0, 1.0), (365, 0.6)]}),
    "htf": (HTF, {"volumes": HTF_VOLUME}),
    "ascending": (ASCENDING, {}),
}


def rows(matches: list[PatternMatch]) -> list[dict[str, object]]:
    return sorted(
        (m.as_row() for m in matches), key=lambda r: (str(r["type"]), str(r["start_date"]))
    )


@pytest.mark.parametrize("name", CHARTS)
def test_no_lookahead_detection_as_of_any_day_ignores_later_bars(name: str) -> None:
    """Indicators computed on the full history and cut at day D give exactly the detections
    of a run that only ever had data up to D."""
    waypoints, options = CHARTS[name]
    full = chart(waypoints, **options)
    for cut in range(200, len(full), 7):
        known = chart(waypoints, cut=cut, **options)
        day = full.dates[cut]
        assert rows(detect(full.until(day))) == rows(detect(known)), (name, day)


@pytest.mark.parametrize("name", CHARTS)
def test_detection_does_not_depend_on_the_price_level(name: str) -> None:
    waypoints, options = CHARTS[name]
    plain = detect(chart(waypoints[:-1] if name == "vcp" else waypoints, **options))
    scaled = detect(chart(waypoints[:-1] if name == "vcp" else waypoints, scale=8, **options))
    assert [(m.type, m.start, m.status, m.quality) for m in scaled] == [
        (m.type, m.start, m.status, m.quality) for m in plain
    ]
    assert [m.pivot for m in scaled] == pytest.approx([m.pivot * 8 for m in plain])

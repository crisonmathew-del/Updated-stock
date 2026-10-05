"""One-day entry events (spec §6.8): pocket pivots and earnings gaps.

Each session in the recent window is judged only with data up to that session, so a day
missed by the nightly run is still found the next night with the same result.
"""

from collections.abc import Sequence
from datetime import date

import numpy as np

from app.patterns.bars import Bars
from app.patterns.context import add_rs_score, finite, linear
from app.patterns.swings import HIGH, LOW
from app.patterns.types import PatternMatch, PatternType, Point, Scorecard, Status
from app.settings.schema import AppSettings

POCKET_PIVOT_RECENT = 3  # sessions checked each run (covers a missed night or two)
EARNINGS_GAP_RECENT = 20  # an earnings gap stays reported (holding or failed) this long
QUARTER = 63


def detect_pocket_pivots(
    bars: Bars, settings: AppSettings, *, base_forming: bool = False
) -> list[PatternMatch]:
    """An up day whose volume beats every down day's volume over the previous
    pocket_pivot_lookback_days sessions, in a Stage 2 stock, closing at most
    pocket_pivot_ma_distance_pct above its 10- or 50-day SMA (or after undercutting and
    reclaiming it), or inside a base that is forming on the last session."""
    s = settings
    lookback = s.pocket_pivot_lookback_days
    t = bars.last
    found = []
    for j in range(max(lookback + 1, t - POCKET_PIVOT_RECENT + 1), t + 1):
        close, previous = bars.close[j], bars.close[j - 1]
        if close <= previous or not finite(bars.stage[j]) or int(bars.stage[j]) != 2:
            continue
        window = range(j - lookback, j)
        down = [bars.volume[k] for k in window if bars.close[k] < bars.close[k - 1]]
        if not down or bars.volume[j] <= max(down):
            continue
        near = []
        for name, average in (("10-day", bars.sma10[j]), ("50-day", bars.sma50[j])):
            if not finite(average) or average <= 0:
                continue
            distance = (close / average - 1) * 100
            if 0 <= distance <= s.pocket_pivot_ma_distance_pct or bars.low[j] <= average <= close:
                near.append((name, distance))
        in_base = base_forming and j == t
        if not near and not in_base:
            continue
        ratio = bars.volume[j] / max(down)
        span = bars.high[j] - bars.low[j]
        place = (close - bars.low[j]) / span if span > 0 else 1.0
        card = Scorecard()
        card.add(
            "volume",
            "Volume above every recent down day",
            35,
            0.4 + 0.6 * linear(ratio, 2.0, 1.0),
            f"Volume {ratio:.2f}× the largest down-day volume of the previous {lookback} sessions.",
        )
        card.add(
            "close",
            "Close in the upper part of the day's range",
            20,
            linear(place, 0.75, 0.25),
            f"Closed {place * 100:.0f}% of the way up the day's range.",
        )
        where = "; ".join(f"{d:.1f}% above the {n} SMA" for n, d in near)
        context = 1.0 if in_base else 0.8 if near and near[0][0] == "10-day" else 0.7
        card.add(
            "context",
            "Near a rising average or inside a base",
            25,
            context,
            ("Inside a forming base. " if in_base else "") + (where + "." if where else ""),
        )
        add_rs_score(card, bars, max(0, j - QUARTER), j, 20)
        found.append(
            PatternMatch(
                type=PatternType.POCKET_PIVOT,
                timeframe="daily",
                start=bars.dates[j],
                end=bars.dates[j],
                pivot=float(close),
                status=Status.FORMING,
                duration_weeks=0.2,
                components=card.components,
                base_low=float(bars.low[j]),
                swings=[Point(bars.dates[j], float(bars.high[j]), HIGH)],
                details={
                    "volume_vs_down_max": round(float(ratio), 2),
                    "day_high": round(float(bars.high[j]), 4),
                    "day_low": round(float(bars.low[j]), 4),
                },
            )
        )
    return found


def detect_earnings_gaps(
    bars: Bars, settings: AppSettings, releases: Sequence[tuple[date, str]] = ()
) -> list[PatternMatch]:
    """A gap up of at least earnings_gap_min_pct (open vs previous close) on at least
    earnings_gap_min_volume_multiple × average volume, with a results release (8-K item 2.02)
    filed on the gap day or up to earnings_gap_catalyst_sessions before it. Valid while
    closes hold above the gap day's low; reported for EARNINGS_GAP_RECENT sessions."""
    s = settings
    t = bars.last
    found = []
    for j in range(max(1, t - EARNINGS_GAP_RECENT + 1), t + 1):
        gap = (bars.open[j] / bars.close[j - 1] - 1) * 100
        average = bars.avg_volume_50[j]
        if gap < s.earnings_gap_min_pct or not finite(average) or average <= 0:
            continue
        multiple = bars.volume[j] / average
        if multiple < s.earnings_gap_min_volume_multiple:
            continue
        window_start = bars.dates[max(0, j - s.earnings_gap_catalyst_sessions)]
        catalysts = [(d, timing) for d, timing in releases if window_start <= d <= bars.dates[j]]
        if not catalysts:
            continue
        release, timing = catalysts[-1]
        gap_low = float(bars.low[j])
        after = bars.close[j + 1 : t + 1]
        holding = not bool(np.any(after < gap_low))
        card = Scorecard()
        card.add(
            "gap",
            "Size of the gap",
            25,
            0.5 + 0.5 * linear(gap, 15, s.earnings_gap_min_pct),
            f"Opened {gap:.1f}% above the previous close (needs {s.earnings_gap_min_pct:g}%).",
        )
        card.add(
            "volume",
            "Volume",
            25,
            0.5 + 0.5 * linear(multiple, 5, s.earnings_gap_min_volume_multiple),
            f"Volume {multiple:.1f}× the 50-day average (needs "
            f"{s.earnings_gap_min_volume_multiple:g}×).",
        )
        reacted = (release == bars.dates[j] and timing == "before_open") or (
            release < bars.dates[j] and timing == "after_close"
        )
        card.add(
            "catalyst",
            "Earnings catalyst",
            20,
            1.0 if reacted else 0.6,
            f"Results released {release.isoformat()} ({timing.replace('_', ' ')}).",
        )
        before = bars.close[max(0, j - QUARTER)]
        prior = (bars.close[j - 1] / before - 1) * 100 if before > 0 else 0.0
        card.add(
            "setup",
            "From a neglected or basing stock",
            15,
            1.0 if prior <= 10 else 0.5 if prior <= 25 else 0.0,
            f"{prior:+.1f}% in the {QUARTER} sessions before the gap.",
        )
        span = bars.high[j] - bars.low[j]
        place = (bars.close[j] - bars.low[j]) / span if span > 0 else 1.0
        card.add(
            "hold",
            "Strong close, holding the gap",
            15,
            0.5 * (place >= 0.5) + 0.5 * holding,
            f"Gap day closed {place * 100:.0f}% up its range; "
            + (
                "closes since have held above the gap-day low."
                if holding
                else "a later close fell below the gap-day low."
            ),
        )
        found.append(
            PatternMatch(
                type=PatternType.EARNINGS_GAP,
                timeframe="daily",
                start=bars.dates[j],
                end=bars.dates[j],
                pivot=float(bars.high[j]),
                status=Status.FORMING if holding else Status.FAILED,
                duration_weeks=0.2,
                components=card.components,
                base_low=gap_low,
                depth_pct=None,
                swings=[
                    Point(bars.dates[j], float(bars.high[j]), HIGH),
                    Point(bars.dates[j], gap_low, LOW),
                ],
                details={
                    "gap_pct": round(float(gap), 2),
                    "volume_multiple": round(float(multiple), 2),
                    "release_date": release.isoformat(),
                    "release_timing": timing,
                    "gap_day_low": round(gap_low, 4),
                },
            )
        )
    return found

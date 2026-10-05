"""Three-weeks-tight (spec §6.7): three or more consecutive weekly closes within
three_weeks_tight_pct of each other, in a Stage 2 stock above its 50-day (10-week) average
after a prior advance. Only completed weeks count. Pivot = the highest weekly high of the run.
"""

import numpy as np

from app.patterns.bars import Bars, WeeklyBars
from app.patterns.bases import WEEK
from app.patterns.context import add_rs_score, breakout_status, linear, qualifying_uptrend
from app.patterns.swings import HIGH, LOW
from app.patterns.types import PatternMatch, PatternType, Point, Scorecard
from app.settings.schema import AppSettings

MIN_WEEKS = 3


def _spread(closes: np.ndarray) -> float:
    return float((closes.max() / closes.min() - 1) * 100)


def detect_three_weeks_tight(
    bars: Bars, settings: AppSettings, weeks: WeeklyBars
) -> PatternMatch | None:
    s = settings
    complete = np.nonzero(weeks.complete)[0]
    if len(complete) < MIN_WEEKS:
        return None
    last = int(complete[-1])
    first = last
    while first > 0 and _spread(weeks.close[first - 1 : last + 1]) <= s.three_weeks_tight_pct:
        first -= 1
    count = last - first + 1
    if count < MIN_WEEKS:
        return None
    start_day, end_day = int(weeks.first[first]), int(weeks.last[last])
    stage = bars.stage[end_day]
    sma50 = bars.sma50[end_day]
    close = bars.close[end_day]
    if not (np.isfinite(stage) and int(stage) == 2 and np.isfinite(sma50) and close > sma50):
        return None
    gain = qualifying_uptrend(bars, start_day, s)
    if gain is None:
        return None
    pivot = float(weeks.high[first : last + 1].max())
    status = breakout_status(bars, pivot, end_day)
    if status is None:
        return None
    end = status.end
    spread = _spread(weeks.close[first : last + 1])
    card = Scorecard()
    card.add(
        "tightness",
        "Tight weekly closes",
        30,
        1 - 0.5 * spread / s.three_weeks_tight_pct,
        f"{count} weekly closes within {spread:.2f}% of each other (limit "
        f"{s.three_weeks_tight_pct:g}%).",
    )
    card.add(
        "length",
        "Weeks in the run",
        10,
        1.0 if count >= 4 else 0.6,
        f"{count} tight weeks.",
    )
    volumes = weeks.volume[first : last + 1]
    falling = volumes[-1] <= volumes[0]
    card.add(
        "volume",
        "Quiet volume",
        20,
        1.0 if falling else 0.4,
        f"Last week's volume was {volumes[-1] / volumes[0] * 100:.0f}% of the first week's.",
    )
    extension = (close / sma50 - 1) * 100
    card.add(
        "trend",
        "Above a rising 10-week line, not extended",
        20,
        linear(extension, 10, 25) if extension > 10 else 1.0,
        f"Close {extension:.1f}% above the 50-day (10-week) average.",
    )
    add_rs_score(card, bars, start_day, end, 20)
    high_day = start_day + int(np.argmax(bars.high[start_day : end_day + 1]))
    low_day = start_day + int(np.argmin(bars.low[start_day : end_day + 1]))
    return PatternMatch(
        type=PatternType.THREE_WEEKS_TIGHT,
        timeframe="weekly",
        start=bars.dates[start_day],
        end=bars.dates[end],
        pivot=pivot,
        status=status.status,
        duration_weeks=float(count) if status.index is None else (end - start_day + 1) / WEEK,
        components=card.components,
        base_low=float(weeks.low[first : last + 1].min()),
        depth_pct=None,
        swings=[
            Point(bars.dates[high_day], float(bars.high[high_day]), HIGH),
            Point(bars.dates[low_day], float(bars.low[low_day]), LOW),
        ],
        details={
            "weekly_closes": [round(float(c), 4) for c in weeks.close[first : last + 1]],
            "spread_pct": round(spread, 2),
            "prior_uptrend_pct": round(gain, 1),
            "breakout_date": None if status.index is None else bars.dates[status.index].isoformat(),
        },
    )

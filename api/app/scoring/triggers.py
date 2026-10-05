"""Secondary entry triggers (spec §6.8) judged on the as-of session with setup context.

- Pullback to a rising average (add-on entry): a leader (RS ≥ `pullback_rs_min`) that broke
  out within `pullback_breakout_within_sessions` pulls back from its post-breakout high on
  lighter volume (the pullback's average volume below the 50-day average), today's low comes
  within `pullback_ma_touch_pct` of the 10- or 21-day EMA, or makes the first test of the
  50-day SMA since the breakout, and it closes up on the day, above that average.
- Undercut & rally: within a base, the low of the last `undercut_within_sessions` sessions
  dipped below a prior swing low of the base by at most `undercut_max_pct`, and today closes
  back above it, up on the day, on at least average volume.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from app.patterns.bars import Bars
from app.settings.schema import AppSettings


@dataclass(frozen=True)
class Trigger:
    type: str  # pullback | undercut_rally
    detail: str
    level: float  # the average or the reclaimed low


def pullback_to_average(
    bars: Bars, settings: AppSettings, *, rs_rating: int | None, breakout_index: int | None
) -> Trigger | None:
    s = settings
    t = bars.last
    if rs_rating is None or rs_rating < s.pullback_rs_min or breakout_index is None:
        return None
    if not 2 <= t - breakout_index <= s.pullback_breakout_within_sessions:
        return None
    peak = breakout_index + int(np.argmax(bars.high[breakout_index:t]))
    if t - peak < 2 or bars.close[t] <= bars.close[t - 1]:
        return None
    average = bars.avg_volume_50[t]
    pullback_volume = float(bars.volume[peak + 1 : t].mean())
    if not np.isfinite(average) or pullback_volume >= average:
        return None
    touch = 1 + s.pullback_ma_touch_pct / 100
    low, close = float(bars.low[t]), float(bars.close[t])
    candidates = (
        ("10-day EMA", bars.ema10),
        ("21-day EMA", bars.ema21),
        ("50-day SMA", bars.sma50),
    )
    for name, line in candidates:
        level = float(line[t])
        if not np.isfinite(level) or not (low <= level * touch and close >= level):
            continue
        if name == "50-day SMA" and np.any(
            bars.low[breakout_index + 1 : t] <= line[breakout_index + 1 : t] * touch
        ):
            continue  # only the first test of the 50-day counts
        return Trigger(
            "pullback",
            f"Pulled back from {bars.high[peak]:.2f} on {pullback_volume / average * 100:.0f}% of "
            f"average volume to the {name} ({level:.2f}) and closed up at {close:.2f}, "
            f"{t - breakout_index} sessions after the breakout.",
            level,
        )
    return None


def undercut_and_rally(
    bars: Bars, settings: AppSettings, *, lows: Sequence[tuple[date, float]]
) -> Trigger | None:
    s = settings
    t = bars.last
    if t < 1 or bars.close[t] <= bars.close[t - 1]:
        return None
    average = bars.avg_volume_50[t]
    if not np.isfinite(average) or bars.volume[t] < average:
        return None
    window_start = max(0, t - s.undercut_within_sessions)
    recent_low = float(bars.low[window_start : t + 1].min())
    cutoff = bars.dates[window_start]
    for day, level in sorted(lows, reverse=True):
        if day >= cutoff:
            continue  # the level must predate the undercut
        depth = (1 - recent_low / level) * 100
        if 0 < depth <= s.undercut_max_pct and bars.close[t] > level >= bars.close[t - 1]:
            return Trigger(
                "undercut_rally",
                f"Undercut the base low of {day.isoformat()} ({level:.2f}) by {depth:.1f}% and "
                f"closed back above it at {bars.close[t]:.2f} on "
                f"{bars.volume[t] / average * 100:.0f}% of average volume.",
                level,
            )
    return None

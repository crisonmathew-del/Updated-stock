"""Red flags (spec §6.9): warnings shown with their numbers, each subtracting a configurable
penalty from the Setup Score. Computed from the bars up to the as-of session only.

- extended: close more than `extended_above_50d_pct` above the 50-day SMA, or more than the
  buy zone (`buy_zone_max_pct_above_pivot`) above the pivot.
- late_stage: base number `late_stage_base_number` or later since Stage 2 began.
- climax: up `climax_gain_pct` or more from the lowest low of the last `climax_max_weeks`.
- wide_and_loose: a week in the base whose range exceeds `wide_loose_weekly_range_pct` and
  that closes in the lower third of that range.
- distribution: `distribution_days_in_base` or more down days in the base on at least
  `distribution_volume_multiple` × the 50-day average volume.
- earnings_soon: results expected within `earnings_warning_days` sessions (no penalty by
  default; marked "earnings risk").
Below-liquidity stocks are never scored, so that flag has no entry here.
"""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np

from app.patterns.bars import Bars
from app.settings.schema import AppSettings

WEEK = 5


@dataclass(frozen=True)
class RedFlag:
    key: str
    label: str
    penalty: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_red_flags(
    bars: Bars,
    settings: AppSettings,
    *,
    pivot: float | None = None,
    base_start: date | None = None,
    base_number: int | None = None,
    next_earnings: date | None = None,
    sessions_to_earnings: int | None = None,
) -> list[RedFlag]:
    s = settings
    p = s.red_flag_penalties
    t = bars.last
    close = float(bars.close[t])
    flags: list[RedFlag] = []

    reasons = []
    sma50 = bars.sma50[t]
    if np.isfinite(sma50) and sma50 > 0:
        above = (close / sma50 - 1) * 100
        if above > s.extended_above_50d_pct:
            reasons.append(
                f"close {above:.1f}% above the 50-day SMA (limit {s.extended_above_50d_pct:g}%)"
            )
    if pivot:
        over = (close / pivot - 1) * 100
        if over > s.buy_zone_max_pct_above_pivot:
            reasons.append(
                f"close {over:.1f}% above the pivot (the buy zone ends at "
                f"{s.buy_zone_max_pct_above_pivot:g}%): don't chase"
            )
    if reasons:
        flags.append(RedFlag("extended", "Extended", p.extended, _sentence("; ".join(reasons))))

    if base_number is not None and base_number >= s.late_stage_base_number:
        flags.append(
            RedFlag(
                "late_stage",
                "Late-stage base",
                p.late_stage,
                f"Base {base_number} since Stage 2 began (late-stage from base "
                f"{s.late_stage_base_number}).",
            )
        )

    window = max(1, round(s.climax_max_weeks * WEEK))
    lows = bars.low[max(0, t - window + 1) : t + 1]
    if len(lows):
        gain = (close / float(lows.min()) - 1) * 100
        if gain >= s.climax_gain_pct:
            flags.append(
                RedFlag(
                    "climax",
                    "Climax run",
                    p.climax,
                    f"Up {gain:.0f}% from the low of the last {s.climax_max_weeks:g} weeks "
                    f"(warning at {s.climax_gain_pct:g}%).",
                )
            )

    if base_start is not None:
        first = int(np.searchsorted(bars.days, np.datetime64(base_start)))
        loose = _wide_and_loose_weeks(bars, first, t, s.wide_loose_weekly_range_pct)
        if loose:
            weeks = ", ".join(d.isoformat() for d in loose)
            flags.append(
                RedFlag(
                    "wide_and_loose",
                    "Wide and loose",
                    p.wide_and_loose,
                    f"{len(loose)} week(s) in the base with a range over "
                    f"{s.wide_loose_weekly_range_pct:g}% closing near the low (week of {weeks}).",
                )
            )
        heavy = _heavy_down_days(bars, first, t, s.distribution_volume_multiple)
        if len(heavy) >= s.distribution_days_in_base:
            flags.append(
                RedFlag(
                    "distribution",
                    "Heavy distribution in the base",
                    p.distribution,
                    f"{len(heavy)} down days in the base on {s.distribution_volume_multiple:g}× "
                    f"average volume or more (warning at {s.distribution_days_in_base}).",
                )
            )

    if (
        next_earnings is not None
        and sessions_to_earnings is not None
        and sessions_to_earnings <= s.earnings_warning_days
    ):
        flags.append(
            RedFlag(
                "earnings_soon",
                "Earnings risk",
                p.earnings_soon,
                f"Results expected {next_earnings.isoformat()}, {sessions_to_earnings} "
                f"session(s) away: a gap either way is possible.",
            )
        )
    return flags


def _sentence(text: str) -> str:
    """Upper-case the first letter only (str.capitalize would lower-case "SMA")."""
    return text[:1].upper() + text[1:] + "."


def _wide_and_loose_weeks(bars: Bars, first: int, last: int, limit_pct: float) -> list[date]:
    """First day of each week in [first, last] whose range exceeds `limit_pct` and closes in
    the lower third of its range."""
    out = []
    keys = [d.isocalendar()[:2] for d in bars.dates[first : last + 1]]
    start = first
    for i in range(first, last + 1):
        end_of_week = i == last or keys[i - first + 1] != keys[i - first]
        if not end_of_week:
            continue
        high = float(bars.high[start : i + 1].max())
        low = float(bars.low[start : i + 1].min())
        close = float(bars.close[i])
        if low > 0 and (high / low - 1) * 100 > limit_pct and close <= low + (high - low) / 3:
            out.append(bars.dates[start])
        start = i + 1
    return out


def _heavy_down_days(bars: Bars, first: int, last: int, multiple: float) -> list[date]:
    out = []
    for i in range(max(first, 1), last + 1):
        average = bars.avg_volume_50[i]
        if (
            bars.close[i] < bars.close[i - 1]
            and np.isfinite(average)
            and bars.volume[i] >= multiple * average
        ):
            out.append(bars.dates[i])
    return out

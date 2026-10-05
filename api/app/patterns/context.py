"""Building blocks shared by the detectors (spec §6.7): prior uptrend, volume dry-up,
tightness, position in the base, base count, RS line behaviour and breakout status, plus the
score components every base uses. All read only bars at or before the index they are given.
"""

from dataclasses import dataclass

import numpy as np

from app.patterns.bars import Bars
from app.patterns.types import Scorecard, Status
from app.settings.schema import AppSettings

# A close above the pivot this many sessions ago or fewer still reports the base as broken out;
# older breakouts are history (the stored row keeps its status).
BREAKOUT_RECENT_SESSIONS = 3
SIX_MONTHS = 126
YEAR = 252


def finite(value: float) -> bool:
    return bool(np.isfinite(value))


def pct(a: float, b: float) -> float:
    """How far `a` is below `b`, in percent of `b`."""
    return (b - a) / b * 100 if b else 0.0


# --- Prior uptrend --------------------------------------------------------------------------


@dataclass(frozen=True)
class Uptrend:
    gain_pct: float
    low_index: int


def prior_uptrend(bars: Bars, start: int, settings: AppSettings) -> Uptrend | None:
    """The advance into the base's left-side high at `start`: from the lowest low in the
    lookback window before it. None if there is no history before the base."""
    first = max(0, start - settings.prior_uptrend_lookback_days)
    if first >= start:
        return None
    low_index = first + int(np.argmin(bars.low[first:start]))
    return Uptrend((bars.high[start] / bars.low[low_index] - 1) * 100, low_index)


# --- Breakout status ------------------------------------------------------------------------


@dataclass(frozen=True)
class Breakout:
    status: Status
    index: int | None  # first close above the pivot, if any
    end: int  # last session of the base (the session before the breakout, or the as-of one)


def breakout_status(bars: Bars, pivot: float, after: int) -> Breakout | None:
    """Status of a base whose pivot is `pivot` and whose right side starts after `after`.
    None when the breakout is older than BREAKOUT_RECENT_SESSIONS (no longer a current base)."""
    t = bars.last
    above = np.nonzero(bars.close[after + 1 : t + 1] > pivot)[0]
    if len(above) == 0:
        return Breakout(Status.FORMING, None, t)
    index = after + 1 + int(above[0])
    if t - index >= BREAKOUT_RECENT_SESSIONS:
        return None
    return Breakout(Status.BROKEN_OUT, index, index - 1)


# --- Volume ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DryUp:
    ratio: float  # average volume of the last 10 sessions ÷ the 50-day average
    quiet_days: int  # sessions in the last 10 below dry_up_day_pct_of_avg of the average


def volume_dry_up(bars: Bars, end: int, settings: AppSettings) -> DryUp | None:
    average = bars.avg_volume_50[end]
    if end < 9 or not finite(average) or average <= 0:
        return None
    recent = bars.volume[end - 9 : end + 1]
    quiet = int(np.sum(recent < settings.dry_up_day_pct_of_avg / 100 * average))
    return DryUp(float(recent.mean() / average), quiet)


def add_volume_score(
    card: Scorecard, bars: Bars, end: int, settings: AppSettings, weight: float
) -> None:
    dry = volume_dry_up(bars, end, settings)
    if dry is None:
        card.add("volume", "Volume dry-up", weight, 0, "Not enough volume history.")
        return
    # Full credit at 70% of average or less, none at 110% or more; quiet days add the rest.
    level = (1.1 - dry.ratio) / 0.4
    fraction = 0.6 * min(1.0, max(0.0, level)) + 0.4 * min(1.0, dry.quiet_days / 3)
    card.add(
        "volume",
        "Volume dry-up",
        weight,
        fraction,
        f"Last 10 sessions averaged {dry.ratio * 100:.0f}% of 50-day volume; {dry.quiet_days} "
        f"session(s) below {settings.dry_up_day_pct_of_avg:g}% of average.",
    )


# --- Tightness ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tightness:
    atr_ratio: float | None  # ATR(10) / ATR(50)
    bb_near_low: bool  # Bollinger width within 10% of its 6-month low
    narrow_days: int  # NR7 or inside days among the last 5 sessions


def tightness(bars: Bars, end: int) -> Tightness:
    ratio = bars.atr_ratio_10_50[end]
    widths = bars.bb_width[max(0, end - SIX_MONTHS + 1) : end + 1]
    widths = widths[np.isfinite(widths)]
    bb_low = bool(len(widths) >= 20 and widths[-1] <= widths.min() * 1.1)
    ranges = bars.high - bars.low
    narrow = 0
    for j in range(max(7, end - 4), end + 1):
        nr7 = ranges[j] <= ranges[j - 6 : j + 1].min()
        inside = bars.high[j] <= bars.high[j - 1] and bars.low[j] >= bars.low[j - 1]
        narrow += bool(nr7 or inside)
    return Tightness(float(ratio) if finite(ratio) else None, bb_low, narrow)


def add_tightness_score(card: Scorecard, bars: Bars, end: int, weight: float) -> None:
    tight = tightness(bars, end)
    parts, fraction = [], 0.0
    if tight.atr_ratio is not None:
        fraction += 0.5 * min(1.0, max(0.0, (1.0 - tight.atr_ratio) / 0.4))
        parts.append(f"ATR(10)/ATR(50) {tight.atr_ratio:.2f}")
    if tight.bb_near_low:
        fraction += 0.3
        parts.append("Bollinger width near its 6-month low")
    else:
        parts.append("Bollinger width not at a 6-month low")
    fraction += 0.2 * min(1.0, tight.narrow_days / 2)
    parts.append(f"{tight.narrow_days} narrow-range/inside day(s) in the last 5")
    card.add("tightness", "Tightness near the pivot", weight, fraction, "; ".join(parts) + ".")


# --- Position, base count, RS ---------------------------------------------------------------


def add_position_score(
    card: Scorecard,
    bars: Bars,
    end: int,
    base_low: float,
    base_high: float,
    pivot: float,
    weight: float,
) -> None:
    close = bars.close[end]
    span = base_high - base_low
    place = (close - base_low) / span if span > 0 else 1.0
    below = pct(close, pivot)
    where = f"{below:.1f}% below the pivot" if below > 0 else f"{-below:.1f}% above the pivot"
    card.add(
        "position",
        "Close in the upper part of the base",
        weight,
        (place - 0.5) / 0.35,
        f"Close is {where}, {place * 100:.0f}% of the way up the base.",
    )


def base_number(bars: Bars, start: int, base_low: float, settings: AppSettings) -> int | None:
    """Which base this is since the current Stage 2 began. A consolidation counts when price
    pulled back at least `base_count_min_correction_pct` for at least the flat-base minimum and
    then made a new high. A base whose low undercuts the previous base's low resets the count
    to 1. None when the stock is in Stage 3 or 4 (not a Stage 2 base); 1 when Stage 2 hasn't
    started yet (a first base out of Stage 1)."""
    stage = bars.stage[start]
    if finite(stage) and int(stage) in (3, 4):
        return None
    if not finite(stage) or int(stage) != 2:
        return 1
    s2 = start
    while s2 > 0 and bars.stage[s2 - 1] == 2:
        s2 -= 1
    min_len = int(settings.flat_base_min_weeks * 5)
    count, previous_low = 0, None
    peak = s2
    for j in range(s2 + 1, start + 1):
        if bars.high[j] <= bars.high[peak]:
            continue
        # New high: was the stretch since the previous high a base?
        if j - peak >= min_len:
            low = float(bars.low[peak:j].min())
            if pct(low, bars.high[peak]) >= settings.base_count_min_correction_pct:
                count = 1 if previous_low is not None and low < previous_low else count + 1
                previous_low = low
        peak = j
    if previous_low is not None and base_low < previous_low:
        return 1
    return count + 1


def add_base_count_score(
    card: Scorecard, number: int | None, settings: AppSettings, weight: float
) -> None:
    late = settings.late_stage_base_number
    if number is None:
        card.add(
            "base_count",
            "Base count",
            weight,
            0,
            "Not a Stage 2 base (the stock is in Stage 3 or 4).",
        )
        return
    fraction = {1: 1.0, 2: 0.8}.get(number, 0.4 if number < late else 0.0)
    note = " (late-stage)" if number >= late else ""
    card.add(
        "base_count", "Base count", weight, fraction, f"Base {number} since Stage 2 began{note}."
    )


def add_rs_score(card: Scorecard, bars: Bars, start: int, end: int, weight: float) -> None:
    rs = bars.rs_line
    if not (finite(rs[start]) and finite(rs[end])) or rs[start] <= 0:
        card.add("rs_line", "RS line in the base", weight, 0, "No relative-strength line yet.")
        return
    change = (rs[end] / rs[start] - 1) * 100
    window = rs[max(0, end - YEAR + 1) : end + 1]
    new_high = bool(rs[end] >= np.nanmax(window))
    fraction = (0.6 if change >= 0 else max(0.0, 0.6 + change / 25)) + (0.4 if new_high else 0)
    high_note = "; RS line at a 52-week high" if new_high else ""
    card.add(
        "rs_line",
        "RS line in the base",
        weight,
        fraction,
        f"RS line {change:+.1f}% since the base began{high_note}.",
    )

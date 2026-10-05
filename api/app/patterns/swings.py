"""Swing highs and lows (ZigZag) with a per-bar reversal threshold.

The default threshold is `swing_atr_multiple` × ATR(14) (spec §6.7: 1.5 × ATR), so a quiet
stock's small contractions still register while a volatile stock's noise doesn't. Detectors
that care about a fixed percentage (ascending-base pullbacks) pass `pct_threshold` instead.

The scan is online: while rising it tracks the highest high; once price falls `threshold`
below it, that high becomes a confirmed swing high (confirmed on the bar that fell far
enough), and it starts tracking the lowest low, and vice versa. So the swings known on bar t
are exactly those with `confirmed_at <= t`: no lookahead. The extreme being tracked is
returned as `pending` (a provisional point the base's right side may still be forming).
"""

from dataclasses import dataclass

import numpy as np

from app.patterns.bars import Floats

HIGH, LOW = "high", "low"


@dataclass(frozen=True, slots=True)
class Swing:
    index: int
    price: float
    kind: str  # high | low
    confirmed_at: int  # bar on which the reversal confirmed it; -1 while pending


def atr_threshold(atr: Floats, multiple: float) -> Floats:
    return atr * multiple


def pct_threshold(close: Floats, pct: float) -> Floats:
    return close * pct / 100


def zigzag(high: Floats, low: Floats, threshold: Floats) -> tuple[list[Swing], Swing | None]:
    """Confirmed swings (alternating high/low, oldest first) and the pending extreme. Bars
    whose threshold is unknown (NaN, e.g. before ATR has 14 bars) can extend an extreme but
    never confirm a reversal."""
    n = len(high)
    if n == 0:
        return [], None
    swings: list[Swing] = []
    direction = 0  # 0 = undecided, 1 = rising (tracking a high), -1 = falling (tracking a low)
    hi = lo = 0
    for i in range(1, n):
        limit = threshold[i]
        usable = bool(np.isfinite(limit)) and limit > 0
        if direction == 0:
            # Undecided: once the range between the highest high and the lowest low reaches
            # the threshold, whichever extreme came first is the first swing.
            if high[i] > high[hi]:
                hi = i
            if low[i] < low[lo]:
                lo = i
            if not usable or high[hi] - low[lo] < limit:
                continue
            if lo < hi:
                swings.append(Swing(lo, float(low[lo]), LOW, i))
                direction = 1
            else:
                swings.append(Swing(hi, float(high[hi]), HIGH, i))
                direction = -1
        elif direction == 1:
            if high[i] > high[hi]:
                hi = i
            elif usable and high[hi] - low[i] >= limit:
                swings.append(Swing(hi, float(high[hi]), HIGH, i))
                direction, lo = -1, i
        else:
            if low[i] < low[lo]:
                lo = i
            elif usable and high[i] - low[lo] >= limit:
                swings.append(Swing(lo, float(low[lo]), LOW, i))
                direction, hi = 1, i
    if direction == 1:
        pending = Swing(hi, float(high[hi]), HIGH, -1)
    elif direction == -1:
        pending = Swing(lo, float(low[lo]), LOW, -1)
    else:
        pending = None
    return swings, pending


def with_pending(swings: list[Swing], pending: Swing | None) -> list[Swing]:
    """Confirmed swings plus the provisional latest extreme, when it differs from the last
    confirmed point."""
    if pending is None or (swings and swings[-1].index == pending.index):
        return list(swings)
    return [*swings, pending]

"""The breakout watch (Phase 8): how ready a base looks to break out soon, as a 0-100 score of
the signs traders look for before a breakout (spec §6.7's volume dry-up and tightness, §6.4's
RS line behaviour), each part with the numbers behind it. Pure, from bars cut at the as-of
session. It ranks; it can't know. The app tracks how often its picks actually broke out.

Parts (points from settings.watch_weights, rescaled over the parts that have data):
- distance: how close the close is to the pivot: full at the pivot (or above it, inside the
  buy zone), nothing at `watch_max_distance_pct` below it;
- tightness: ATR(10) / ATR(50): full at `watch_tight_ratio_full` or less, nothing at
  `watch_tight_ratio_zero` (the daily swings are shrinking);
- dry_up: the last 10 sessions' average volume / the 50-day average: full at
  `watch_dry_up_ratio_full` or less, nothing at `watch_dry_up_ratio_zero` (sellers are gone);
- relative_strength: the RS line at a new 52-week high ahead of price (full), at a 52-week
  high (0.8), rising over 3 months (0.4);
- demand: the 50-day up/down volume ratio, full at `watch_demand_ratio_full`, nothing at
  `watch_demand_ratio_zero`; a pocket pivot in the last 10 sessions adds 0.4 of the part;
- support: where the last `watch_support_sessions` closes sat in their day's range on
  average, full at `watch_support_full`, nothing at `watch_support_zero` (buyers step in).

Early entry: when the last `watch_early_entry_sessions` sessions form a tight area (high to low
at most `watch_early_entry_max_range_pct`) whose high is at least
`watch_early_entry_min_below_pct` below the pivot, that high is an earlier buy point
(entered like a pivot) and its low (less the stop buffer) the stop. Riskier: the base may
never break out.
"""

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from app.patterns.bars import Bars
from app.risk.trade_plan import down_to_cent, entry_price
from app.scoring.verdict import shares_for
from app.settings.schema import AppSettings

RECENT = 10  # sessions: the dry-up window, and pocket pivots that count as recent


@dataclass(frozen=True)
class WatchPart:
    key: str
    label: str
    points: float
    max_points: float
    detail: str


@dataclass(frozen=True)
class WatchScore:
    score: float  # 0-100
    parts: list[WatchPart]
    early_entry: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "parts": [asdict(p) for p in self.parts],
            "early_entry": self.early_entry,
        }


def _scale(value: float, zero_at: float, full_at: float) -> float:
    """0 at `zero_at`, 1 at `full_at` (either direction), clipped."""
    if full_at == zero_at:
        return 1.0 if value == full_at else 0.0
    return float(min(1.0, max(0.0, (value - zero_at) / (full_at - zero_at))))


def _finite(x: float) -> float | None:
    v = float(x)
    return v if np.isfinite(v) else None


def watch_score(
    bars: Bars,
    settings: AppSettings,
    *,
    pivot: float,
    rs_new_high_ahead: bool,
    rs_line_high_52w: bool,
    rs_slope_63: float | None,
    up_down_volume: float | None,
    pocket_pivots_recent: int,
) -> WatchScore:
    """The readiness of a base with `pivot`, as of the last bar in `bars`."""
    s, w, t = settings, settings.watch_weights, bars.last
    close = float(bars.close[t])
    zone_top = pivot * (1 + s.buy_zone_max_pct_above_pivot / 100)
    below = round((pivot - close) / close * 100, 2)  # % below the pivot, as readiness_pct
    parts: list[tuple[str, str, float, float | None, str]] = []  # key, label, weight, fraction

    if close > zone_top:
        fraction, detail = 0.0, f"{-below:.1f}% above the pivot: past the buy zone."
    elif below <= 0:
        fraction, detail = 1.0, f"At the pivot ({-below:.1f}% above it), inside the buy zone."
    else:
        fraction = _scale(below, s.watch_max_distance_pct, 0.0)
        detail = f"{below:.1f}% below the pivot {pivot:.2f}."
    parts.append(("distance", "Close to the buy point", w.distance, fraction, detail))

    ratio = _finite(bars.atr_ratio_10_50[t])
    if ratio is None:
        parts.append(
            ("tightness", "Price swings shrinking", w.tightness, None, "Not enough history.")
        )
    else:
        fraction = _scale(ratio, s.watch_tight_ratio_zero, s.watch_tight_ratio_full)
        parts.append(
            (
                "tightness",
                "Price swings shrinking",
                w.tightness,
                fraction,
                f"Daily swings over the last 10 days are {ratio:.2f}× the 50-day average.",
            )
        )

    average = _finite(bars.avg_volume_50[t])
    if average is None or average <= 0 or t + 1 < RECENT:
        parts.append(
            ("dry_up", "Quiet volume (sellers gone)", w.dry_up, None, "Not enough history.")
        )
    else:
        recent = float(np.mean(bars.volume[t - RECENT + 1 : t + 1]))
        quiet = recent / average
        fraction = _scale(quiet, s.watch_dry_up_ratio_zero, s.watch_dry_up_ratio_full)
        parts.append(
            (
                "dry_up",
                "Quiet volume (sellers gone)",
                w.dry_up,
                fraction,
                f"Volume over the last 10 days is {quiet:.2f}× the 50-day average.",
            )
        )

    if rs_new_high_ahead:
        fraction, detail = 1.0, "The RS line is at a new 52-week high before the price is."
    elif rs_line_high_52w:
        fraction, detail = 0.8, "The RS line is at a 52-week high."
    elif rs_slope_63 is not None and rs_slope_63 > 0:
        fraction, detail = 0.4, "The RS line is rising over 3 months."
    else:
        fraction, detail = 0.0, "The RS line isn't rising."
    parts.append(
        ("relative_strength", "Beating the market now", w.relative_strength, fraction, detail)
    )

    if up_down_volume is None:
        parts.append(("demand", "Buyers outweigh sellers", w.demand, None, "No volume ratio yet."))
    else:
        fraction = _scale(up_down_volume, s.watch_demand_ratio_zero, s.watch_demand_ratio_full)
        detail = f"Volume on up days is {up_down_volume:.2f}× volume on down days (50 days)."
        if pocket_pivots_recent > 0:
            fraction = min(1.0, fraction + 0.4)
            many = "s" if pocket_pivots_recent > 1 else ""
            detail += f" {pocket_pivots_recent} pocket pivot{many} in the last {RECENT} days."
        parts.append(("demand", "Buyers outweigh sellers", w.demand, fraction, detail))

    n = min(s.watch_support_sessions, t + 1)
    highs, lows = bars.high[t - n + 1 : t + 1], bars.low[t - n + 1 : t + 1]
    closes = bars.close[t - n + 1 : t + 1]
    spans = highs - lows
    places = np.where(spans > 0, (closes - lows) / np.where(spans > 0, spans, 1), 0.5)
    place = float(np.mean(places))
    fraction = _scale(place, s.watch_support_zero, s.watch_support_full)
    parts.append(
        (
            "support",
            "Closing near the day's highs",
            w.support,
            fraction,
            f"The last {n} closes averaged {place * 100:.0f}% up their day's range.",
        )
    )

    scored = [(k, label, weight, f, d) for k, label, weight, f, d in parts if f is not None]
    total_weight = sum(weight for _, _, weight, _, _ in scored)
    scale = 100 / total_weight if total_weight else 0.0
    out = [
        WatchPart(
            k,
            label,
            round((f or 0.0) * weight * scale, 1) if f is not None else 0.0,
            round(weight * scale, 1) if f is not None else 0.0,
            d,
        )
        for k, label, weight, f, d in parts
    ]
    score = round(sum((f or 0.0) * weight * scale for _, _, weight, f, _ in scored), 1)
    return WatchScore(score, out, early_entry(bars, settings, pivot=pivot))


def early_entry(bars: Bars, settings: AppSettings, *, pivot: float) -> dict[str, Any] | None:
    """An earlier buy point at the top of a tight area just under the pivot, or None."""
    s, t = settings, bars.last
    n = s.watch_early_entry_sessions
    if t + 1 < n:
        return None
    high = float(np.max(bars.high[t - n + 1 : t + 1]))
    low = float(np.min(bars.low[t - n + 1 : t + 1]))
    if high <= 0 or low <= 0:
        return None
    spread = (high - low) / high * 100
    entry = entry_price(high, s)
    below_pivot = (pivot - high) / pivot * 100
    if (
        spread > s.watch_early_entry_max_range_pct
        or below_pivot < s.watch_early_entry_min_below_pct - 1e-9
        or entry >= entry_price(pivot, s)
    ):
        return None
    stop = down_to_cent(low * (1 - s.stop_buffer_pct / 100))
    risk_pct = (entry - stop) / entry * 100
    if stop >= entry or risk_pct > s.max_stop_loss_pct:
        return None
    shares = shares_for(entry, stop, s)
    return {
        "entry": entry,
        "stop": stop,
        "shares": shares,
        "risk_pct": round(risk_pct, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "sessions": n,
        "spread_pct": round(spread, 2),
        "text": f"Earlier, riskier entry: a buy-stop for {shares} shares at {entry:.2f} (the top "
        f"of the last {n} days' tight range, {spread:.1f}% from high to low), stop {stop:.2f} "
        f"({risk_pct:.1f}% risk). It buys before the breakout, so the base may never break out.",
    }


__all__ = ["WatchPart", "WatchScore", "early_entry", "watch_score"]

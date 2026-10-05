"""Market regime (spec §6.1): the "M" in CAN SLIM, checked before anything else.

For each index (SPY, QQQ, and IWM in small-cap mode) a day-by-day state machine tracks:

- Distribution days: close down at least `distribution_day_min_drop_pct` on higher volume than
  the prior day. Counted over the last `distribution_day_lookback_sessions`; one expires early
  once the index closes `distribution_day_expiry_gain_pct` above it. The count starts afresh
  from a follow-through day (old selling no longer counts against a new uptrend).
- Rally attempts and follow-through days: in a correction, the first up close after the low
  starts a rally attempt (day 1). Trading below the attempt's low resets it. A follow-through
  day is day `ftd_min_day` or later, up at least `ftd_min_gain_pct` on higher volume.

States and transitions (thresholds from settings):

- CORRECTION: entered when the index breaks below its 50-day SMA while breadth is weak (fewer
  than `breadth_weak_pct_above_50`% of stocks above their 50-day; if breadth is unknown the
  price rule applies alone), when distribution days reach `regime_correction_distribution_days`,
  or when a recent follow-through fails (rally low undercut within
  `ftd_failure_window_sessions`). Left only by a follow-through day.
- UPTREND_UNDER_PRESSURE: distribution days at `regime_pressure_distribution_days`, the index
  below its 21-day EMA while above its 50-day, or below its 50-day with healthy breadth.

"Breaks below its 50-day" means the index had been above the 50-day since the last
follow-through. Follow-through days usually happen below the 50-day with weak breadth; the new
uptrend isn't cancelled for that, only for failing to hold the rally low or heavy selling.
- CONFIRMED_UPTREND: after a follow-through day; regained from "under pressure" once
  distribution days are at most `regime_confirmed_max_distribution_days` and the index is back
  above its 21-day EMA.

The overall market state is the weakest of the indexes considered.
"""

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import polars as pl

from app.settings.schema import AppSettings


class RegimeState(StrEnum):
    CONFIRMED_UPTREND = "confirmed_uptrend"
    UPTREND_UNDER_PRESSURE = "uptrend_under_pressure"
    CORRECTION = "correction"


SEVERITY = {
    RegimeState.CONFIRMED_UPTREND: 0,
    RegimeState.UPTREND_UNDER_PRESSURE: 1,
    RegimeState.CORRECTION: 2,
}

LABELS = {
    RegimeState.CONFIRMED_UPTREND: "Confirmed uptrend",
    RegimeState.UPTREND_UNDER_PRESSURE: "Uptrend under pressure",
    RegimeState.CORRECTION: "Correction",
}

MARKET = "MARKET"


@dataclass
class RegimeDay:
    date: dt.date
    index_symbol: str
    state: RegimeState
    close: float | None = None
    ema21: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    change_pct: float | None = None
    is_distribution_day: bool = False
    distribution_days: int = 0
    distribution_dates: list[dt.date] = field(default_factory=list)
    rally_day: int | None = None
    rally_low: float | None = None
    is_ftd: bool = False
    last_ftd_date: dt.date | None = None
    pct_above_50: float | None = None
    changed_from: RegimeState | None = None
    reasons: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "index_symbol": self.index_symbol,
            "state": self.state.value,
            "close": self.close,
            "ema21": self.ema21,
            "sma50": self.sma50,
            "sma200": self.sma200,
            "change_pct": self.change_pct,
            "is_distribution_day": self.is_distribution_day,
            "distribution_days": self.distribution_days,
            "distribution_dates": [d.isoformat() for d in self.distribution_dates],
            "rally_day": self.rally_day,
            "rally_low": self.rally_low,
            "is_ftd": self.is_ftd,
            "last_ftd_date": self.last_ftd_date,
            "pct_above_50": self.pct_above_50,
            "changed_from": self.changed_from.value if self.changed_from else None,
            "reasons": self.reasons,
        }


@dataclass
class _Distribution:
    index: int
    date: dt.date
    close: float


def _fmt_dates(dates: list[dt.date]) -> str:
    return ", ".join(d.strftime("%b %-d") for d in dates)


def compute_index_regime(
    index_symbol: str,
    bars: pl.DataFrame,
    settings: AppSettings,
    breadth: dict[dt.date, float] | None = None,
) -> list[RegimeDay]:
    """`bars`: date, high, low, close, volume, ema21, sma50 (sma200 optional), one index, any
    order. `breadth`: % of stocks above their 50-day SMA by date. Rows before the 21-day EMA and
    50-day SMA exist are skipped."""
    rows = bars.sort("date").to_dicts()
    breadth = breadth or {}
    min_drop = settings.distribution_day_min_drop_pct / 100
    ftd_gain = settings.ftd_min_gain_pct / 100
    expiry = settings.distribution_day_expiry_gain_pct / 100

    days: list[RegimeDay] = []
    state: RegimeState | None = None
    distributions: list[_Distribution] = []
    correction_low: float | None = None
    rally_day: int | None = None
    rally_low: float | None = None
    last_ftd: tuple[int, dt.date, float] | None = None  # (row index, date, rally low)
    above_50_since_ftd = False
    prev: dict[str, Any] | None = None

    for i, row in enumerate(rows):
        if prev is None:
            prev = row
            continue
        close, low, volume = row["close"], row["low"], row["volume"]
        change = close / prev["close"] - 1
        higher_volume = volume > prev["volume"]
        prev = row
        if row.get("ema21") is None or row.get("sma50") is None:
            continue

        # Distribution days: add today's, then drop expired and aged-out ones.
        is_dd = change <= -min_drop and higher_volume
        if is_dd:
            distributions.append(_Distribution(i, row["date"], close))
        distributions = [
            d
            for d in distributions
            if i - d.index < settings.distribution_day_lookback_sessions
            and not (d.index < i and close >= d.close * (1 + expiry))
        ]
        # A distribution day expires permanently once the index has closed 5% above it, even if
        # it later falls back; the filter above removes it on that day and it never returns.

        pct50 = breadth.get(row["date"])
        weak_breadth = pct50 is None or pct50 < settings.breadth_weak_pct_above_50
        day = RegimeDay(
            date=row["date"],
            index_symbol=index_symbol,
            state=RegimeState.CORRECTION,
            close=close,
            ema21=row["ema21"],
            sma50=row["sma50"],
            sma200=row.get("sma200"),
            change_pct=round(change * 100, 4),
            is_distribution_day=is_dd,
            pct_above_50=pct50,
        )
        previous_state = state
        transition: str | None = None

        above_50 = close > row["sma50"]
        if state is None:
            state = RegimeState.CONFIRMED_UPTREND if above_50 else RegimeState.CORRECTION
            correction_low = low if state is RegimeState.CORRECTION else None
            above_50_since_ftd = above_50
        elif state is RegimeState.CORRECTION:
            if rally_day is not None and rally_low is not None:
                if low < rally_low:
                    transition = (
                        f"Rally attempt reset: {index_symbol} undercut the low of {rally_low:,.2f}"
                    )
                    rally_day, rally_low, correction_low = None, None, low
                else:
                    rally_day += 1
                    if rally_day >= settings.ftd_min_day and change >= ftd_gain and higher_volume:
                        state = RegimeState.CONFIRMED_UPTREND
                        day.is_ftd = True
                        last_ftd = (i, row["date"], rally_low)
                        transition = (
                            f"Follow-through day: day {rally_day} of the rally attempt, "
                            f"{change * 100:+.2f}% on higher volume"
                        )
                        distributions = [d for d in distributions if d.index > i]
                        rally_day, rally_low, correction_low = None, None, None
                        above_50_since_ftd = above_50
            else:
                correction_low = low if correction_low is None else min(correction_low, low)
                if change > 0:
                    rally_day, rally_low = 1, correction_low
                    transition = f"Rally attempt day 1 (low {correction_low:,.2f})"
        else:
            failed_ftd = (
                last_ftd is not None
                and i - last_ftd[0] <= settings.ftd_failure_window_sessions
                and low < last_ftd[2]
            )
            broke_50 = not above_50 and above_50_since_ftd
            above_50_since_ftd = above_50_since_ftd or above_50
            dd_count = len(distributions)
            if failed_ftd:
                state = RegimeState.CORRECTION
                transition = "Follow-through failed: the rally low was undercut"
            elif broke_50 and weak_breadth:
                state = RegimeState.CORRECTION
                transition = f"{index_symbol} broke below its 50-day SMA with weak breadth"
            elif dd_count >= settings.regime_correction_distribution_days:
                state = RegimeState.CORRECTION
                window = settings.distribution_day_lookback_sessions
                transition = f"{dd_count} distribution days in {window} sessions"
            elif (
                dd_count >= settings.regime_pressure_distribution_days
                or (close < row["ema21"] and above_50)
                or broke_50
            ):
                state = RegimeState.UPTREND_UNDER_PRESSURE
            elif (
                state is RegimeState.UPTREND_UNDER_PRESSURE
                and dd_count <= settings.regime_confirmed_max_distribution_days
                and close > row["ema21"]
            ):
                state = RegimeState.CONFIRMED_UPTREND
            if state is RegimeState.CORRECTION:
                correction_low, rally_day, rally_low = low, None, None

        day.state = state
        day.distribution_days = len(distributions)
        day.distribution_dates = [d.date for d in distributions]
        day.rally_day = rally_day if state is RegimeState.CORRECTION else None
        day.rally_low = rally_low if state is RegimeState.CORRECTION else None
        day.last_ftd_date = last_ftd[1] if last_ftd else None
        if previous_state is not None and previous_state is not state:
            day.changed_from = previous_state
        day.reasons = _reasons(day, transition, settings)
        days.append(day)
    return days


def _reasons(day: RegimeDay, transition: str | None, settings: AppSettings) -> list[str]:
    symbol = day.index_symbol
    reasons = []
    if transition:
        reasons.append(transition)
    if day.close is not None and day.sma50:
        side = "above" if day.close >= day.sma50 else "below"
        reasons.append(
            f"{symbol} {abs(day.close / day.sma50 - 1) * 100:.1f}% {side} its 50-day SMA "
            f"({day.close:,.2f} vs {day.sma50:,.2f})"
        )
    if day.close is not None and day.ema21:
        side = "above" if day.close >= day.ema21 else "below"
        reasons.append(f"{symbol} {side} its 21-day EMA ({day.ema21:,.2f})")
    count = day.distribution_days
    window = settings.distribution_day_lookback_sessions
    if count:
        reasons.append(
            f"{count} distribution day{'s' if count != 1 else ''} in the last {window} sessions "
            f"({_fmt_dates(day.distribution_dates)})"
        )
    else:
        reasons.append(f"No distribution days in the last {window} sessions")
    if day.rally_day:
        reasons.append(
            f"Rally attempt day {day.rally_day}; a follow-through needs day "
            f"{settings.ftd_min_day}+ up {settings.ftd_min_gain_pct:g}% on higher volume"
        )
    if day.last_ftd_date and day.state is not RegimeState.CORRECTION:
        reasons.append(f"Last follow-through day {day.last_ftd_date.isoformat()}")
    if day.pct_above_50 is not None:
        reasons.append(f"{day.pct_above_50:.0f}% of stocks are above their 50-day SMA")
    return reasons


def combine_market(per_index: dict[str, list[RegimeDay]]) -> list[RegimeDay]:
    """The overall market on each date: the weakest state among the indexes that have one."""
    by_date: dict[dt.date, list[RegimeDay]] = {}
    for days in per_index.values():
        for d in days:
            by_date.setdefault(d.date, []).append(d)
    market: list[RegimeDay] = []
    previous: RegimeState | None = None
    for day_date in sorted(by_date):
        entries = by_date[day_date]
        worst = max(entries, key=lambda d: SEVERITY[d.state])
        reasons = [
            f"{d.index_symbol}: {LABELS[d.state].lower()}"
            for d in sorted(entries, key=lambda d: d.index_symbol)
        ]
        combined = RegimeDay(
            date=day_date,
            index_symbol=MARKET,
            state=worst.state,
            distribution_days=max(d.distribution_days for d in entries),
            is_ftd=any(d.is_ftd for d in entries),
            last_ftd_date=max((d.last_ftd_date for d in entries if d.last_ftd_date), default=None),
            pct_above_50=worst.pct_above_50,
            changed_from=previous if previous is not None and previous is not worst.state else None,
            reasons=reasons + worst.reasons,
        )
        previous = worst.state
        market.append(combined)
    return market

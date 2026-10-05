"""Minervini's Trend Template (spec §6.3): eight checks that must all pass for a trend leader.

`add_trend_template` evaluates every row of an indicator frame (vectorised, for screening and
group leadership counts); `explain_trend_template` turns one row into the checklist the UI
shows, with the actual numbers. Missing inputs (short histories) fail the check.
"""

from dataclasses import dataclass
from typing import Any

import polars as pl

from app.indicators.frame import per_ticker
from app.settings.schema import AppSettings

CHECKS: tuple[tuple[str, str], ...] = (
    ("above_150_200", "Price above the 150-day and 200-day averages"),
    ("150_above_200", "150-day average above the 200-day average"),
    ("200_rising", "200-day average rising"),
    ("50_above_150_200", "50-day average above the 150-day and 200-day averages"),
    ("above_50", "Price above the 50-day average"),
    ("above_52w_low", "Price far enough above the 52-week low"),
    ("near_52w_high", "Price close enough to the 52-week high"),
    ("rs_rating", "RS Rating high enough"),
)


def add_sma200_ago(df: pl.DataFrame, lookback: int) -> pl.DataFrame:
    """The 200-day SMA `lookback` sessions earlier (needs that much history in `df`)."""
    return df.with_columns(per_ticker(pl.col("sma200").shift(lookback)).alias("sma200_ago"))


def evaluate_trend_template(df: pl.DataFrame, settings: AppSettings) -> pl.DataFrame:
    """Row-wise checks; needs close, sma50/150/200, sma200_ago, low_52w, high_52w, rs_rating.
    Adds tt_<check> booleans, tt_passed (count) and tt_pass (all eight)."""
    c = pl.col
    conditions = {
        "above_150_200": (c("close") > c("sma150")) & (c("close") > c("sma200")),
        "150_above_200": c("sma150") > c("sma200"),
        "200_rising": c("sma200") > c("sma200_ago"),
        "50_above_150_200": (c("sma50") > c("sma150")) & (c("sma50") > c("sma200")),
        "above_50": c("close") > c("sma50"),
        "above_52w_low": c("close") >= c("low_52w") * (1 + settings.pct_above_52w_low_min / 100),
        "near_52w_high": c("close") >= c("high_52w") * (1 - settings.pct_below_52w_high_max / 100),
        "rs_rating": c("rs_rating") >= settings.rs_rating_min,
    }
    df = df.with_columns(
        expr.fill_null(False).alias(f"tt_{key}") for key, expr in conditions.items()
    )
    flags = [c(f"tt_{key}") for key, _ in CHECKS]
    return df.with_columns(
        pl.sum_horizontal(f.cast(pl.Int8) for f in flags).alias("tt_passed"),
        pl.all_horizontal(flags).alias("tt_pass"),
    )


def add_trend_template(df: pl.DataFrame, settings: AppSettings) -> pl.DataFrame:
    """Both steps on a frame sorted by ticker and date with enough history."""
    return evaluate_trend_template(
        add_sma200_ago(df, settings.ma200_uptrend_lookback_days), settings
    )


@dataclass(frozen=True)
class CheckResult:
    key: str
    label: str
    passed: bool
    detail: str


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


def explain_trend_template(row: dict[str, Any], settings: AppSettings) -> list[CheckResult]:
    """One row of `add_trend_template` output → the eight checks with their numbers."""
    close, s50, s150, s200 = row["close"], row["sma50"], row["sma150"], row["sma200"]
    s200_ago, low, high, rs = row["sma200_ago"], row["low_52w"], row["high_52w"], row["rs_rating"]
    lookback = settings.ma200_uptrend_lookback_days

    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:,.2f}"

    details = {
        "above_150_200": f"Close {fmt(close)} vs 150-day {fmt(s150)} and 200-day {fmt(s200)}",
        "150_above_200": f"150-day {fmt(s150)} vs 200-day {fmt(s200)}",
        "200_rising": (
            f"200-day {fmt(s200)} vs {fmt(s200_ago)} {lookback} sessions ago"
            + (f" ({_pct(s200, s200_ago):+.1f}%)" if s200 and s200_ago else "")
        ),
        "50_above_150_200": f"50-day {fmt(s50)} vs 150-day {fmt(s150)} and 200-day {fmt(s200)}",
        "above_50": f"Close {fmt(close)} vs 50-day {fmt(s50)}",
        "above_52w_low": (
            f"{_pct(close, low):+.1f}% above the 52-week low {fmt(low)} "
            f"(needs +{settings.pct_above_52w_low_min:g}%)"
            if close and low
            else "Not enough history"
        ),
        "near_52w_high": (
            f"{_pct(close, high):+.1f}% from the 52-week high {fmt(high)} "
            f"(needs within {settings.pct_below_52w_high_max:g}%)"
            if close and high
            else "Not enough history"
        ),
        "rs_rating": (
            f"RS Rating {rs} (needs {settings.rs_rating_min})"
            if rs is not None
            else "No RS Rating yet"
        ),
    }
    return [
        CheckResult(key, label, bool(row.get(f"tt_{key}")), details[key]) for key, label in CHECKS
    ]

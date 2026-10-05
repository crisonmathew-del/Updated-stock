"""Compute every per-ticker indicator for a frame of daily bars."""

import polars as pl

from app.indicators.atr import add_atr
from app.indicators.frame import DATE, TICKER
from app.indicators.moving_averages import add_ema, add_slope, add_sma
from app.indicators.ranges import add_52_week_range
from app.indicators.relative_strength import add_rs_line, add_rs_raw
from app.indicators.stage import add_stage
from app.indicators.volatility import add_atr_ratio, add_bollinger_width
from app.indicators.volume import add_up_down_volume_ratio, add_volume_averages

# Columns stored in indicators_daily (besides ticker_id, date, rs_rating).
INDICATOR_COLUMNS: tuple[str, ...] = (
    "ema10",
    "ema21",
    "sma50",
    "sma150",
    "sma200",
    "sma50_slope",
    "sma150_slope",
    "sma200_slope",
    "atr14",
    "atr_ratio_10_50",
    "bb_width",
    "high_52w",
    "low_52w",
    "close_high_52w",
    "history_sessions",
    "avg_volume_50",
    "avg_dollar_volume_50",
    "volume_ratio",
    "up_down_volume_50",
    "roc_63",
    "roc_126",
    "roc_189",
    "roc_252",
    "rs_raw",
    "rs_line",
    "rs_line_high_52w",
    "rs_new_high_ahead",
    "rs_slope_21",
    "rs_slope_63",
    "stage",
)

SLOPE_LOOKBACK = 21


def compute_indicators(
    bars: pl.DataFrame,
    benchmark: pl.DataFrame | None,
    *,
    stage_lookback: int = 20,
    stage_flat_pct: float = 1.0,
) -> pl.DataFrame:
    """`bars`: ticker_id, date, open, high, low, close, volume (any number of tickers).
    `benchmark`: date, close of SPY for the RS line (None → RS line columns are null).
    Returns ticker_id, date, close, high, low, volume plus INDICATOR_COLUMNS."""
    df = bars.sort(TICKER, DATE)
    df = add_ema(df, 10)
    df = add_ema(df, 21)
    for n in (50, 150, 200):
        df = add_sma(df, n)
    df = add_slope(df, "sma50", SLOPE_LOOKBACK, "sma50_slope")
    df = add_slope(df, "sma200", SLOPE_LOOKBACK, "sma200_slope")
    df = add_atr(df, 14)
    df = add_atr_ratio(df, 10, 50)
    df = add_bollinger_width(df)
    df = add_52_week_range(df)
    df = add_volume_averages(df, 50)
    df = add_up_down_volume_ratio(df, 50)
    df = add_rs_raw(df)
    if benchmark is not None and not benchmark.is_empty():
        df = add_rs_line(df, benchmark)
    else:
        df = df.with_columns(
            pl.lit(None, pl.Float64).alias(c) for c in ("rs_line", "rs_slope_21", "rs_slope_63")
        ).with_columns(
            pl.lit(False).alias("rs_line_high_52w"), pl.lit(False).alias("rs_new_high_ahead")
        )
    df = add_stage(df, stage_lookback, stage_flat_pct)
    return df.select(TICKER, DATE, "close", "high", "low", "volume", *INDICATOR_COLUMNS)

"""Volatility contraction measures (used for tightness in Phase 3)."""

import polars as pl

from app.indicators.atr import add_atr
from app.indicators.frame import per_ticker


def add_atr_ratio(df: pl.DataFrame, fast: int = 10, slow: int = 50) -> pl.DataFrame:
    """ATR(fast) / ATR(slow). Falling values mean the daily range is contracting."""
    df = add_atr(df, fast, f"_atr{fast}")
    df = add_atr(df, slow, f"_atr{slow}")
    return df.with_columns(
        pl.when(pl.col(f"_atr{slow}") > 0)
        .then(pl.col(f"_atr{fast}") / pl.col(f"_atr{slow}"))
        .otherwise(None)
        .alias(f"atr_ratio_{fast}_{slow}")
    ).drop(f"_atr{fast}", f"_atr{slow}")


def add_bollinger_width(df: pl.DataFrame, n: int = 20, k: float = 2.0) -> pl.DataFrame:
    """(upper - lower) / middle = 2k·sd / SMA, with the population standard deviation (as
    TradingView and StockCharts use)."""
    mean = per_ticker(pl.col("close").rolling_mean(window_size=n))
    std = per_ticker(pl.col("close").rolling_std(window_size=n, ddof=0))
    return df.with_columns((2 * k * std / mean).alias("bb_width"))

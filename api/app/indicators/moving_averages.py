"""Simple and exponential moving averages of the close."""

import polars as pl

from app.indicators.frame import per_ticker, seeded_smoothing


def add_sma(
    df: pl.DataFrame, n: int, source: str = "close", name: str | None = None
) -> pl.DataFrame:
    """Simple moving average; null until `n` bars exist."""
    return df.with_columns(
        per_ticker(pl.col(source).rolling_mean(window_size=n)).alias(name or f"sma{n}")
    )


def add_ema(
    df: pl.DataFrame, n: int, source: str = "close", name: str | None = None
) -> pl.DataFrame:
    """Exponential moving average, alpha = 2/(n+1), seeded with the SMA of the first n bars."""
    return seeded_smoothing(df, source, n, 2 / (n + 1), name or f"ema{n}")


def add_slope(df: pl.DataFrame, source: str, lookback: int, name: str) -> pl.DataFrame:
    """Fractional change of `source` over `lookback` bars (0.02 = up 2%)."""
    return df.with_columns(
        per_ticker(pl.col(source) / pl.col(source).shift(lookback) - 1).alias(name)
    )

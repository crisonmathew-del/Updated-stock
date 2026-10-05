"""Weinstein stage analysis (spec §6.2) from the 150-day (30-week) SMA.

The SMA's slope over `lookback` sessions decides whether it is rising, falling or flat
(|slope| below `flat_pct`). Then:
- Stage 2: rising MA, close above it.      - Stage 4: falling MA, close below it.
- Rising MA but close below it → Stage 3 (possible top).  Falling MA but close above → Stage 1.
- Flat MA → Stage 3 if the last clear trend was up (topping), else Stage 1 (basing).
The "last clear trend" is carried forward per ticker, so no state machine (or lookahead) is
needed.
"""

import polars as pl

from app.indicators.frame import per_ticker


def add_stage(df: pl.DataFrame, lookback: int = 20, flat_pct: float = 1.0) -> pl.DataFrame:
    flat = flat_pct / 100
    slope = per_ticker(pl.col("sma150") / pl.col("sma150").shift(lookback) - 1)
    df = df.with_columns(slope.alias("sma150_slope"))
    trend = (
        pl.when(pl.col("sma150_slope") >= flat)
        .then(1)
        .when(pl.col("sma150_slope") <= -flat)
        .then(-1)
        .otherwise(None)
    )
    df = df.with_columns(per_ticker(trend.forward_fill()).alias("_last_trend"))
    rising = pl.col("sma150_slope") >= flat
    falling = pl.col("sma150_slope") <= -flat
    above = pl.col("close") > pl.col("sma150")
    stage = (
        pl.when(pl.col("sma150_slope").is_null())
        .then(None)
        .when(rising & above)
        .then(2)
        .when(falling & ~above)
        .then(4)
        .when(rising)
        .then(3)
        .when(falling)
        .then(1)
        .when(pl.col("_last_trend") == 1)
        .then(3)
        .otherwise(1)
    )
    return df.with_columns(stage.cast(pl.Int8).alias("stage")).drop("_last_trend")

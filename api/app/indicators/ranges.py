"""52-week highs and lows."""

import polars as pl

from app.indicators.frame import per_ticker

SESSIONS_PER_YEAR = 252


def add_52_week_range(df: pl.DataFrame, window: int = SESSIONS_PER_YEAR) -> pl.DataFrame:
    """Highest intraday high, lowest intraday low and highest close over the last `window`
    sessions including today. Shorter histories (recent listings) use what they have, like
    charting platforms do."""
    return df.with_columns(
        per_ticker(pl.col("high").rolling_max(window_size=window, min_samples=1)).alias("high_52w"),
        per_ticker(pl.col("low").rolling_min(window_size=window, min_samples=1)).alias("low_52w"),
        per_ticker(pl.col("close").rolling_max(window_size=window, min_samples=1)).alias(
            "close_high_52w"
        ),
        per_ticker(pl.int_range(1, pl.len() + 1)).alias("history_sessions"),
    )

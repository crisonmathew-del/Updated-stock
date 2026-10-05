"""True range and Wilder's Average True Range."""

import polars as pl

from app.indicators.frame import per_ticker, seeded_smoothing


def add_true_range(df: pl.DataFrame, name: str = "true_range") -> pl.DataFrame:
    """max(high - low, |high - prior close|, |low - prior close|); the first bar uses high - low."""
    prev_close = per_ticker(pl.col("close").shift(1))
    return df.with_columns(
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        ).alias(name)
    )


def add_atr(df: pl.DataFrame, n: int, name: str | None = None) -> pl.DataFrame:
    """Wilder's ATR: RMA of true range with alpha = 1/n, seeded with the first n-bar average.
    Matches TradingView's `ta.atr`."""
    if "true_range" not in df.columns:
        df = add_true_range(df)
    return seeded_smoothing(df, "true_range", n, 1 / n, name or f"atr{n}")

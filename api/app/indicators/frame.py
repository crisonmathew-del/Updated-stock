"""Helpers shared by the indicator modules."""

import polars as pl

TICKER = "ticker_id"
DATE = "date"


def per_ticker(expr: pl.Expr) -> pl.Expr:
    return expr.over(TICKER)


def row_index() -> pl.Expr:
    """0-based position of each row within its ticker."""
    return pl.int_range(pl.len()).over(TICKER)


def seeded_smoothing(
    df: pl.DataFrame, source: str, n: int, alpha: float, name: str
) -> pl.DataFrame:
    """Exponential smoothing seeded with the simple average of the first `n` values, the
    textbook definition used for EMA and Wilder's RMA: value[n-1] = mean(x[0:n]),
    value[t] = alpha·x[t] + (1 - alpha)·value[t-1]. Earlier rows are null."""
    seed, start = f"_{name}_seed", f"_{name}_start"
    return (
        df.with_columns(
            per_ticker(pl.col(source).rolling_mean(window_size=n)).alias(seed),
            row_index().alias(start),
        )
        .with_columns(
            pl.when(pl.col(start) < n - 1)
            .then(None)
            .when(pl.col(start) == n - 1)
            .then(pl.col(seed))
            .otherwise(pl.col(source))
            .alias(seed)
        )
        .with_columns(per_ticker(pl.col(seed).ewm_mean(alpha=alpha, adjust=False)).alias(name))
        .drop(seed, start)
    )

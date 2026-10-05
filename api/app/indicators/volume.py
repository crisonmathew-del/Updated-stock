"""Volume averages and accumulation measures."""

import polars as pl

from app.indicators.frame import per_ticker

AVERAGE_MIN_SAMPLES = 20


def add_volume_averages(
    df: pl.DataFrame, n: int = 50, min_samples: int = AVERAGE_MIN_SAMPLES
) -> pl.DataFrame:
    """Average volume and dollar volume over the `n` sessions *before* today, so a breakout
    day's volume is compared with a baseline it doesn't inflate. Needs 20 sessions, so recent
    listings get a value early. `volume_ratio` = today's volume / that average."""
    avg_volume = per_ticker(
        pl.col("volume")
        .cast(pl.Float64)
        .shift(1)
        .rolling_mean(window_size=n, min_samples=min_samples)
    )
    avg_dollar = per_ticker(
        (pl.col("close") * pl.col("volume"))
        .shift(1)
        .rolling_mean(window_size=n, min_samples=min_samples)
    )
    return df.with_columns(
        avg_volume.alias(f"avg_volume_{n}"),
        avg_dollar.alias(f"avg_dollar_volume_{n}"),
    ).with_columns(
        pl.when(pl.col(f"avg_volume_{n}") > 0)
        .then(pl.col("volume") / pl.col(f"avg_volume_{n}"))
        .otherwise(None)
        .alias("volume_ratio")
    )


def add_up_down_volume_ratio(df: pl.DataFrame, n: int = 50) -> pl.DataFrame:
    """Volume on up closes ÷ volume on down closes over the last `n` sessions (including
    today); unchanged closes count for neither. ≥ 1.2 suggests accumulation. Null when there
    was no down volume."""
    volume = pl.col("volume").cast(pl.Float64)
    with_change = df.with_columns(per_ticker(pl.col("close").diff()).alias("_change"))
    up = pl.when(pl.col("_change") > 0).then(volume).otherwise(0.0)
    down = pl.when(pl.col("_change") < 0).then(volume).otherwise(0.0)
    return (
        with_change.with_columns(
            per_ticker(up.rolling_sum(window_size=n, min_samples=1)).alias("_up"),
            per_ticker(down.rolling_sum(window_size=n, min_samples=1)).alias("_down"),
        )
        .with_columns(
            pl.when(pl.col("_down") > 0)
            .then(pl.col("_up") / pl.col("_down"))
            .otherwise(None)
            .alias(f"up_down_volume_{n}")
        )
        .drop("_change", "_up", "_down")
    )

"""Relative strength (spec §6.4).

- RS raw: 0.4·ROC(63) + 0.2·ROC(126) + 0.2·ROC(189) + 0.2·ROC(252). Stocks with less than a
  year of history use the periods they have, with the weights rescaled to sum to 1; at least
  63 sessions are required.
- RS Rating (1-99): percentile of RS raw among all eligible stocks on the same date.
- RS line: close ÷ SPY close. "New high ahead of price": the RS line is at a 52-week high
  while the price is still below its 52-week closing high.
"""

import polars as pl

from app.indicators.frame import DATE, TICKER, per_ticker
from app.indicators.ranges import SESSIONS_PER_YEAR

RS_WEIGHTS: tuple[tuple[int, float], ...] = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))
RS_LINE_MIN_SESSIONS = 63


def add_roc(df: pl.DataFrame, periods: tuple[int, ...] = (63, 126, 189, 252)) -> pl.DataFrame:
    return df.with_columns(
        per_ticker(pl.col("close") / pl.col("close").shift(n) - 1).alias(f"roc_{n}")
        for n in periods
    )


def add_rs_raw(df: pl.DataFrame) -> pl.DataFrame:
    df = add_roc(df, tuple(n for n, _ in RS_WEIGHTS))
    numerator = pl.sum_horizontal(pl.col(f"roc_{n}").fill_null(0) * w for n, w in RS_WEIGHTS)
    weights = pl.sum_horizontal(
        pl.when(pl.col(f"roc_{n}").is_not_null()).then(w).otherwise(0.0) for n, w in RS_WEIGHTS
    )
    first = RS_WEIGHTS[0][0]
    return df.with_columns(
        pl.when(pl.col(f"roc_{first}").is_not_null())
        .then(numerator / weights)
        .otherwise(None)
        .alias("rs_raw")
    )


def rs_ratings(raw: pl.DataFrame) -> pl.DataFrame:
    """Rank `rs_raw` across tickers on each date. Input columns: ticker_id, date, rs_raw (only
    eligible stocks). Returns ticker_id, date, rs_rating (Int16, 1-99; ties share a rating)."""
    ranked = (
        raw.filter(pl.col("rs_raw").is_not_null())
        .with_columns(
            pl.col("rs_raw").rank("average").over(DATE).alias("_rank"),
            pl.len().over(DATE).alias("_count"),
        )
        .with_columns(
            pl.when(pl.col("_count") >= 2)
            .then(1 + ((pl.col("_rank") - 1) / (pl.col("_count") - 1) * 98.999).floor())
            .otherwise(None)
            .cast(pl.Int16)
            .alias("rs_rating")
        )
    )
    return ranked.select(TICKER, DATE, "rs_rating")


def add_rs_line(df: pl.DataFrame, benchmark: pl.DataFrame) -> pl.DataFrame:
    """`benchmark` has columns date, close (SPY). Adds rs_line, rs_line_high_52w,
    rs_new_high_ahead, rs_slope_21, rs_slope_63."""
    bench = benchmark.select(DATE, pl.col("close").alias("_bench_close"))
    df = df.join(bench, on=DATE, how="left").sort(TICKER, DATE)
    df = df.with_columns((pl.col("close") / pl.col("_bench_close")).alias("rs_line"))
    rs_max = per_ticker(pl.col("rs_line").rolling_max(window_size=SESSIONS_PER_YEAR, min_samples=1))
    rs_count = per_ticker(pl.col("rs_line").is_not_null().cum_sum())
    df = df.with_columns(
        ((pl.col("rs_line") >= rs_max) & (rs_count >= RS_LINE_MIN_SESSIONS))
        .fill_null(False)
        .alias("rs_line_high_52w"),
        per_ticker(pl.col("rs_line") / pl.col("rs_line").shift(21) - 1).alias("rs_slope_21"),
        per_ticker(pl.col("rs_line") / pl.col("rs_line").shift(63) - 1).alias("rs_slope_63"),
    )
    return df.with_columns(
        (pl.col("rs_line_high_52w") & (pl.col("close") < pl.col("close_high_52w")))
        .fill_null(False)
        .alias("rs_new_high_ahead")
    ).drop("_bench_close")

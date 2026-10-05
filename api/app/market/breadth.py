"""Market breadth (spec §6.1): participation across the stock universe.

Per date: % of stocks above their 50- and 200-day SMAs, new 52-week highs and lows (stocks
with a full year of history), advancers and decliners, and the cumulative advance/decline line.
Counts are additive, so they can be computed chunk by chunk and summed.
"""

import polars as pl

from app.indicators.frame import DATE, per_ticker
from app.indicators.ranges import SESSIONS_PER_YEAR

COUNT_COLUMNS = (
    "members",
    "with_50",
    "above_50",
    "with_200",
    "above_200",
    "new_highs",
    "new_lows",
    "advancers",
    "decliners",
)


def breadth_counts(ind: pl.DataFrame) -> pl.DataFrame:
    """Counts per date for a chunk of stocks. `ind` (sorted by ticker, date) needs close, high,
    low, sma50, sma200, high_52w, low_52w, history_sessions, and optionally prev_close
    (otherwise derived from the previous row, so a ticker's first row has none)."""
    c = pl.col
    full_year = c("history_sessions") >= SESSIONS_PER_YEAR
    if "prev_close" in ind.columns:
        df = ind.with_columns(c("prev_close").alias("_prev_close"))
    else:
        df = ind.with_columns(per_ticker(c("close").shift(1)).alias("_prev_close"))
    # Boolean sums come back as unsigned integers; cast to signed so differences
    # (net new highs, advancers - decliners) can go negative.
    return (
        df.group_by(DATE)
        .agg(
            pl.len().alias("members"),
            c("sma50").is_not_null().sum().alias("with_50"),
            (c("close") > c("sma50")).fill_null(False).sum().alias("above_50"),
            c("sma200").is_not_null().sum().alias("with_200"),
            (c("close") > c("sma200")).fill_null(False).sum().alias("above_200"),
            (full_year & (c("high") >= c("high_52w"))).fill_null(False).sum().alias("new_highs"),
            (full_year & (c("low") <= c("low_52w"))).fill_null(False).sum().alias("new_lows"),
            (c("close") > c("_prev_close")).fill_null(False).sum().alias("advancers"),
            (c("close") < c("_prev_close")).fill_null(False).sum().alias("decliners"),
        )
        .cast(dict.fromkeys(COUNT_COLUMNS, pl.Int64))
    )


def finalize_breadth(counts: pl.DataFrame, ad_line_start: float = 0.0) -> pl.DataFrame:
    """Sum chunk counts per date and derive percentages and the A/D line, continuing from
    `ad_line_start` (the stored value on the day before the first date)."""
    c = pl.col
    totals = counts.group_by(DATE).agg(c(name).sum() for name in COUNT_COLUMNS).sort(DATE)
    return totals.with_columns(
        pl.when(c("with_50") > 0).then(c("above_50") / c("with_50") * 100).alias("pct_above_50"),
        pl.when(c("with_200") > 0)
        .then(c("above_200") / c("with_200") * 100)
        .alias("pct_above_200"),
        (c("new_highs") - c("new_lows")).alias("net_new_highs"),
        (ad_line_start + (c("advancers") - c("decliners")).cum_sum()).alias("ad_line"),
    )

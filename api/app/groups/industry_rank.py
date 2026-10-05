"""Industry group ranking (spec §6.6).

Per date, each group with at least `MIN_RANKED_MEMBERS` stocks gets:
- median RS Rating of its members, and equal-weighted 3- and 6-month member returns;
- score = 0.5 × percentile(median RS) + 0.25 × percentile(3-month) + 0.25 × percentile(6-month);
- rank 1..N by score (ties: higher median RS, then group id);
- leadership: members passing the Trend Template and members at a 52-week high.
The 4-week rank trend is the change versus 20 sessions earlier (positive = improving).
"""

import polars as pl

MIN_RANKED_MEMBERS = 3
RANK_TREND_SESSIONS = 20
WEIGHTS = {"median_rs": 0.5, "return_3m": 0.25, "return_6m": 0.25}


def group_member_rows(ind: pl.DataFrame, membership: dict[int, int]) -> pl.DataFrame:
    """Reduce an indicator frame (with tt_pass) to the columns group ranking needs.
    `membership` maps ticker_id → group_id; tickers without a group are dropped."""
    groups = pl.DataFrame(
        {"ticker_id": list(membership), "group_id": list(membership.values())},
        schema={"ticker_id": pl.Int32, "group_id": pl.Int32},
    )
    return ind.join(groups, on="ticker_id", how="inner").select(
        "group_id",
        "date",
        "rs_rating",
        pl.col("roc_63").alias("return_3m"),
        pl.col("roc_126").alias("return_6m"),
        pl.col("tt_pass").fill_null(False),
        (pl.col("high") >= pl.col("high_52w")).fill_null(False).alias("at_high"),
    )


def rank_groups(members: pl.DataFrame) -> pl.DataFrame:
    """`members`: output of group_member_rows (any number of dates). Returns one row per ranked
    group per date: group_id, date, rank, score, members, median_rs, return_3m, return_6m,
    tt_passing, new_highs."""
    c = pl.col
    per_group = (
        members.group_by("group_id", "date")
        .agg(
            pl.len().alias("members"),
            c("rs_rating").median().alias("median_rs"),
            c("return_3m").mean().alias("return_3m"),
            c("return_6m").mean().alias("return_6m"),
            c("tt_pass").sum().alias("tt_passing"),
            c("at_high").sum().alias("new_highs"),
        )
        .cast({"members": pl.Int64, "tt_passing": pl.Int64, "new_highs": pl.Int64})
    )
    rankable = per_group.filter((c("members") >= MIN_RANKED_MEMBERS) & c("median_rs").is_not_null())

    def percentile(name: str) -> pl.Expr:
        rank = c(name).rank("average").over("date")
        count = c(name).count().over("date")
        return pl.when(count > 1).then((rank - 1) / (count - 1)).otherwise(0.5).fill_null(0.5)

    score = pl.sum_horizontal(WEIGHTS[name] * percentile(name) for name in WEIGHTS)
    scored = rankable.with_columns(score.alias("score"))
    return (
        scored.sort(
            ["date", "score", "median_rs", "group_id"], descending=[False, True, True, False]
        )
        .with_columns(pl.int_range(1, pl.len() + 1).over("date").cast(pl.Int16).alias("rank"))
        .select(
            "group_id",
            "date",
            "rank",
            "score",
            "members",
            "median_rs",
            "return_3m",
            "return_6m",
            "tt_passing",
            "new_highs",
        )
    )


def add_rank_trend(history: pl.DataFrame) -> pl.DataFrame:
    """Add rank_change_4w = rank 20 sessions earlier - rank today, per group, over the dates in
    `history` (which must include those earlier sessions). Positive means improving."""
    sessions = history.select("date").unique().sort("date").with_row_index("_session")
    indexed = history.join(sessions, on="date")
    earlier = indexed.select(
        "group_id",
        (pl.col("_session") + RANK_TREND_SESSIONS).alias("_session"),
        pl.col("rank").alias("_rank_before"),
    )
    return (
        indexed.join(earlier, on=["group_id", "_session"], how="left")
        .with_columns(
            (pl.col("_rank_before").cast(pl.Int32) - pl.col("rank").cast(pl.Int32))
            .cast(pl.Int16)
            .alias("rank_change_4w")
        )
        .drop("_session", "_rank_before")
        .sort("date", "rank")
    )


def sector_rotation(etfs: pl.DataFrame) -> pl.DataFrame:
    """Rank sector ETFs by RS raw on each date. `etfs`: symbol, date, rs_raw."""
    return etfs.filter(pl.col("rs_raw").is_not_null()).with_columns(
        pl.col("rs_raw").rank("ordinal", descending=True).over("date").cast(pl.Int16).alias("rank")
    )

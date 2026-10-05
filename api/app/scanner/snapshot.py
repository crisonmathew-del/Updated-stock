"""Every stock's indicator row on one session, with the Trend Template evaluated: shared by the
setups stage (one session's evaluation) and the screener (the latest session for everyone)."""

from collections.abc import Sequence
from datetime import date

import polars as pl

from app.core.calendar import previous_session, sessions_back
from app.data.loaders import read_frame
from app.scoring.trend_template import evaluate_trend_template
from app.settings.schema import AppSettings

SNAPSHOT_COLUMNS = (
    "ema10",
    "ema21",
    "sma50",
    "sma150",
    "sma200",
    "low_52w",
    "high_52w",
    "avg_volume_50",
    "avg_dollar_volume_50",
    "volume_ratio",
    "up_down_volume_50",
    "rs_rating",
    "rs_line_high_52w",
    "rs_new_high_ahead",
    "rs_slope_63",
    "stage",
)


def _where_ids(ids: Sequence[int] | None, column: str = "ticker_id") -> str:
    if ids is None:
        return ""
    return f" AND {column} IN ({','.join(str(int(i)) for i in ids)})"


async def indicators_on(
    as_of: date, settings: AppSettings, ids: Sequence[int] | None = None
) -> pl.DataFrame:
    """One row per ticker with a bar and indicators on `as_of` (all tickers, or `ids`): the
    session's OHLCV, the previous close, SNAPSHOT_COLUMNS, `ahead_before` (RS new high ahead
    of price on the previous session) and the Trend Template (`tt_passed` is null without
    200 sessions of history; `tt_pass`). Empty when nothing matches."""
    if ids is not None and not ids:
        return pl.DataFrame()
    columns = ", ".join(f"i.{c}" for c in SNAPSHOT_COLUMNS)
    frame = await read_frame(
        "SELECT i.ticker_id, b.open, b.high, b.low, b.close, b.volume, "
        f"{columns} FROM indicators_daily i JOIN daily_bars b ON b.ticker_id = i.ticker_id "
        f"AND b.date = i.date WHERE i.date = '{as_of.isoformat()}'" + _where_ids(ids, "i.ticker_id")
    )
    if frame.is_empty():
        return frame
    ago = sessions_back(as_of, settings.ma200_uptrend_lookback_days)
    before = previous_session(as_of)
    extras = (
        await read_frame(
            "SELECT ticker_id, sma200 AS sma200_ago FROM indicators_daily "
            f"WHERE date = '{ago.isoformat()}'" + _where_ids(ids)
        ),
        await read_frame(
            "SELECT ticker_id, rs_new_high_ahead AS ahead_before FROM indicators_daily "
            f"WHERE date = '{before.isoformat()}'" + _where_ids(ids)
        ),
        await read_frame(
            "SELECT ticker_id, close AS prev_close FROM daily_bars "
            f"WHERE date = '{before.isoformat()}'" + _where_ids(ids)
        ),
    )
    for extra in extras:
        if not extra.is_empty():
            frame = frame.join(extra, on="ticker_id", how="left")
    for name, dtype in (
        ("sma200_ago", pl.Float64),
        ("ahead_before", pl.Boolean),
        ("prev_close", pl.Float64),
    ):
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(name))
    has_history = frame["sma200"].is_not_null()
    evaluated = evaluate_trend_template(frame, settings)
    return evaluated.with_columns(
        pl.when(has_history).then(pl.col("tt_passed").cast(pl.Int16)).alias("tt_passed")
    )

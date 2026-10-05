"""Bulk reads from Postgres into Polars (via connectorx, which streams Arrow without going
through Python objects). Used by data-quality checks now and the scan pipeline from Phase 2."""

import asyncio
from collections.abc import Sequence
from datetime import date

import polars as pl
from sqlalchemy.engine import make_url

from app.core.config import get_settings

BAR_SCHEMA = {
    "ticker_id": pl.Int32,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
}


def _connectorx_uri() -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _read(query: str) -> pl.DataFrame:
    return pl.read_database_uri(query, _connectorx_uri(), engine="connectorx")


async def read_frame(query: str) -> pl.DataFrame:
    """Run a read-only query (no parameters: inline only trusted ints/dates) into Polars."""
    return await asyncio.to_thread(_read, query)


def _append(table: str, frame: pl.DataFrame) -> None:
    frame.write_database(
        table, connection=_connectorx_uri(), engine="adbc", if_table_exists="append"
    )


async def append_frame(table: str, frame: pl.DataFrame) -> None:
    """Bulk-append via ADBC (binary COPY from Arrow) on its own connection. Column types must
    match the table exactly (Int32 → integer, Int16 → smallint, Float64 → double precision)."""
    if not frame.is_empty():
        await asyncio.to_thread(_append, table, frame)


async def load_bars(
    start: date, end: date, ticker_ids: Sequence[int] | None = None
) -> pl.DataFrame:
    """Daily bars between `start` and `end` (inclusive), sorted by ticker then date."""
    where = [f"date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"]
    if ticker_ids is not None:
        if not ticker_ids:
            return pl.DataFrame(schema=BAR_SCHEMA)
        ids = ",".join(str(int(tid)) for tid in ticker_ids)
        where.append(f"ticker_id IN ({ids})")
    query = (
        "SELECT ticker_id, date, open, high, low, close, volume FROM daily_bars "
        f"WHERE {' AND '.join(where)} ORDER BY ticker_id, date"
    )
    frame = await asyncio.to_thread(_read, query)
    if frame.is_empty():
        return pl.DataFrame(schema=BAR_SCHEMA)
    return frame.cast(BAR_SCHEMA)  # type: ignore[arg-type]

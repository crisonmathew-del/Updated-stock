"""Bulk reads from Postgres into Polars (via connectorx, which streams Arrow without going
through Python objects) for the jobs and the scan pipeline, plus `query_frame` for the small
reads of request handlers."""

import asyncio
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import polars as pl
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

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


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"))  # as connectorx writes it
    return value


async def query_frame(session: AsyncSession, query: str) -> pl.DataFrame:
    """`read_frame` for the small reads of request handlers: it runs on the session's pooled
    connection, where connectorx opens a new connection per query (~20 ms each). The frame
    matches connectorx's: JSON as text, numerics as floats, all-null columns as Float64."""
    result = await session.execute(text(query))
    columns = list(result.keys())
    rows = [tuple(_plain(v) for v in row) for row in result.all()]
    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(columns, pl.Float64))
    frame = pl.DataFrame(rows, schema=columns, orient="row", infer_schema_length=None)
    return frame.with_columns(
        pl.col(c).cast(pl.Float64) for c, dtype in frame.schema.items() if dtype == pl.Null
    )


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

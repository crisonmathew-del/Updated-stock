"""`query_frame` (pooled, for request handlers) returns what `read_frame` (connectorx) does."""

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.loaders import query_frame, read_frame

QUERIES = [
    # dates, floats, ints, text, booleans, an all-null column, numeric, JSONB
    "SELECT d::date AS day, d::date - date '2026-01-01' AS n, 1.5::float8 * g AS price, "
    "g::bigint AS volume, 'x' || g AS label, g % 2 = 0 AS even, NULL::float8 AS missing, "
    "(g / 4.0)::numeric(10, 2) AS ratio, jsonb_build_object('g', g) AS details "
    "FROM generate_series(1, 5) AS g, LATERAL (SELECT date '2026-01-01' + g AS d) x ORDER BY g",
    # no rows
    "SELECT 1::int AS a, 'x'::text AS b WHERE false",
]


@pytest.mark.integration
@pytest.mark.parametrize("query", QUERIES)
async def test_query_frame_matches_connectorx(db: AsyncSession, query: str) -> None:
    pooled = await query_frame(db, query)
    bulk = await read_frame(query)
    assert pooled.columns == bulk.columns
    if bulk.is_empty():
        assert pooled.is_empty()
        return
    # connectorx returns integers as Int64 and JSON as text, like the pooled frame.
    assert_frame_equal(pooled, bulk, check_dtypes=False)
    assert pooled.schema["missing"] == pl.Float64
    assert pooled.schema["ratio"] == pl.Float64
    assert pooled["details"][0] == '{"g":1}'


@pytest.mark.integration
async def test_query_frame_reads_app_tables(db: AsyncSession) -> None:
    await db.execute(
        text(
            "INSERT INTO tickers (symbol, name, exchange, type, active, first_seen, last_seen) "
            "VALUES ('SPOT', 'Spotify', 'NYSE', 'common', true, '2026-01-02', '2026-01-02')"
        )
    )
    await db.commit()
    frame = await query_frame(db, "SELECT symbol, type, is_benchmark FROM tickers")
    assert frame.to_dicts() == [{"symbol": "SPOT", "type": "common", "is_benchmark": False}]

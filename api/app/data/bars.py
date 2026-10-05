"""Writing daily bars and corporate actions.

Bars go through COPY into a temporary table and one INSERT … ON CONFLICT, which is an order of
magnitude faster than row-by-row inserts for a 10-year backfill (~13M rows). Rows whose values
didn't change are left alone, so re-fetching overlapping windows is cheap.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CorporateAction
from app.providers.base import ActionKind, PriceHistory

_BAR_COLUMNS = ["ticker_id", "date", "open", "high", "low", "close", "volume", "vwap", "source"]

_CREATE_STAGING = text(
    """
    CREATE TEMP TABLE IF NOT EXISTS staging_daily_bars (
        ticker_id integer, date date, open double precision, high double precision,
        low double precision, close double precision, volume bigint, vwap double precision,
        source varchar(16)
    ) ON COMMIT DELETE ROWS
    """
)

_MERGE_STAGING = text(
    """
    INSERT INTO daily_bars (ticker_id, date, open, high, low, close, volume, vwap, source)
    SELECT ticker_id, date, open, high, low, close, volume, vwap, source FROM staging_daily_bars
    ON CONFLICT (ticker_id, date) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
        volume = EXCLUDED.volume, vwap = EXCLUDED.vwap, source = EXCLUDED.source
    WHERE (daily_bars.open, daily_bars.high, daily_bars.low, daily_bars.close,
           daily_bars.volume, daily_bars.vwap)
          IS DISTINCT FROM
          (EXCLUDED.open, EXCLUDED.high, EXCLUDED.low, EXCLUDED.close,
           EXCLUDED.volume, EXCLUDED.vwap)
    """
)


async def upsert_bars(
    session: AsyncSession, histories: Mapping[int, PriceHistory], source: str
) -> int:
    """Upsert bars for {ticker_id: history}. Returns rows inserted or changed. Does not commit."""
    records: list[tuple[Any, ...]] = [
        (tid, b.date, b.open, b.high, b.low, b.close, b.volume, b.vwap, source)
        for tid, history in histories.items()
        for b in history.bars
    ]
    if not records:
        return 0
    connection = await session.connection()
    await connection.execute(_CREATE_STAGING)
    raw = await connection.get_raw_connection()
    driver: Any = raw.driver_connection
    await driver.copy_records_to_table("staging_daily_bars", records=records, columns=_BAR_COLUMNS)
    result = await connection.execute(_MERGE_STAGING)
    await connection.execute(text("TRUNCATE staging_daily_bars"))
    return int(getattr(result, "rowcount", 0) or 0)


async def upsert_actions(
    session: AsyncSession, histories: Mapping[int, PriceHistory], source: str
) -> list[tuple[int, date, float]]:
    """Store splits and dividends. Returns splits that weren't known before as
    (ticker_id, ex_date, ratio), so callers can re-fetch history they invalidate.
    Does not commit."""
    splits = [
        {"ticker_id": tid, "ex_date": a.ex_date, "kind": a.kind, "value": a.value, "source": source}
        for tid, h in histories.items()
        for a in h.actions
        if a.kind is ActionKind.SPLIT
    ]
    dividends = [
        {"ticker_id": tid, "ex_date": a.ex_date, "kind": a.kind, "value": a.value, "source": source}
        for tid, h in histories.items()
        for a in h.actions
        if a.kind is ActionKind.DIVIDEND
    ]
    new_splits: list[tuple[int, date, float]] = []
    for chunk in _chunks(splits, 2000):
        result = await session.execute(
            insert(CorporateAction)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["ticker_id", "ex_date", "kind"])
            .returning(CorporateAction.ticker_id, CorporateAction.ex_date, CorporateAction.value)
        )
        new_splits.extend((row[0], row[1], row[2]) for row in result.all())
    for chunk in _chunks(dividends, 2000):
        stmt = insert(CorporateAction).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["ticker_id", "ex_date", "kind"],
                set_={"value": stmt.excluded.value, "source": stmt.excluded.source},
            )
        )
    return new_splits


def _chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]

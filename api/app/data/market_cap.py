"""Market capitalisation from our own data: close × shares outstanding.

Point-in-time rule: use the latest share count *filed* on or before the date, and scale it by any
splits with an ex-date after the count's as-of date (a 4-for-1 split quadruples the share count;
the filing won't reflect it until the next report). Prices are split-adjusted to today, so the
latest close is already as-traded.
"""

import math
from collections.abc import Iterable
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def market_cap(close: float, shares: int, splits_after: Iterable[float] = ()) -> float:
    return close * shares * math.prod(splits_after)


_UPDATE_LATEST = text(
    """
    WITH latest_bar AS (
        SELECT DISTINCT ON (ticker_id) ticker_id, date, close
        FROM daily_bars
        WHERE date >= :since
        ORDER BY ticker_id, date DESC
    ),
    latest_shares AS (
        SELECT DISTINCT ON (s.ticker_id) s.ticker_id, s.as_of_date, s.shares
        FROM shares_outstanding s
        JOIN latest_bar b ON b.ticker_id = s.ticker_id AND s.filed_date <= b.date
        ORDER BY s.ticker_id, s.filed_date DESC, s.as_of_date DESC
    ),
    splits_after AS (
        SELECT s.ticker_id, exp(sum(ln(ca.value))) AS factor
        FROM latest_shares s
        JOIN latest_bar b ON b.ticker_id = s.ticker_id
        JOIN corporate_actions ca
          ON ca.ticker_id = s.ticker_id AND ca.kind = 'split'
         AND ca.ex_date > s.as_of_date AND ca.ex_date <= b.date AND ca.value > 0
        GROUP BY s.ticker_id
    ),
    caps AS (
        SELECT b.ticker_id, b.close * s.shares * coalesce(sa.factor, 1) AS market_cap
        FROM latest_bar b
        JOIN latest_shares s ON s.ticker_id = b.ticker_id
        LEFT JOIN splits_after sa ON sa.ticker_id = b.ticker_id
    )
    UPDATE tickers t
    SET market_cap = caps.market_cap
    FROM caps
    WHERE t.id = caps.ticker_id AND t.type = 'common'
    """
)


async def update_latest_market_caps(session: AsyncSession, since: date) -> int:
    """Refresh `tickers.market_cap` for common stocks with a bar on or after `since`."""
    result = await session.execute(_UPDATE_LATEST, {"since": since})
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)

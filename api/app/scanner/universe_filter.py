"""Which stocks pass the scan's liquidity filters on a given date (spec §5.3), point in time.

Prices in `daily_bars` are split-adjusted to today, so for a past date the as-traded price is
the adjusted close × every split ratio with an ex-date after that date. Market cap is that
as-traded close × the latest share count filed by then, scaled by splits between the count's
as-of date and the date; equivalently adjusted close × shares × every split after the count's
as-of date. Dollar volume needs no adjustment (the split factors of price and volume cancel).
Stocks without a known market cap (ADRs, no SEC share count) are not excluded on market cap.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TickerType
from app.settings.schema import AppSettings

_QUERY = text(
    """
    WITH bar AS (
        SELECT b.ticker_id, b.close, i.avg_dollar_volume_50
        FROM daily_bars b
        JOIN indicators_daily i ON i.ticker_id = b.ticker_id AND i.date = b.date
        WHERE b.date = :day
    ),
    price_factor AS (
        SELECT ca.ticker_id, exp(sum(ln(ca.value))) AS factor
        FROM corporate_actions ca
        JOIN bar ON bar.ticker_id = ca.ticker_id
        WHERE ca.kind = 'split' AND ca.ex_date > :day AND ca.value > 0
        GROUP BY ca.ticker_id
    ),
    shares AS (
        SELECT DISTINCT ON (s.ticker_id) s.ticker_id, s.as_of_date, s.shares
        FROM shares_outstanding s
        JOIN bar ON bar.ticker_id = s.ticker_id
        WHERE s.filed_date <= :day
        ORDER BY s.ticker_id, s.filed_date DESC, s.as_of_date DESC
    ),
    share_factor AS (
        SELECT s.ticker_id, exp(sum(ln(ca.value))) AS factor
        FROM shares s
        JOIN corporate_actions ca
          ON ca.ticker_id = s.ticker_id AND ca.kind = 'split'
         AND ca.ex_date > s.as_of_date AND ca.value > 0
        GROUP BY s.ticker_id
    )
    SELECT t.id, t.type,
           bar.close * coalesce(pf.factor, 1) AS price,
           bar.avg_dollar_volume_50 AS dollar_volume,
           bar.close * s.shares * coalesce(sf.factor, 1) AS market_cap
    FROM tickers t
    JOIN bar ON bar.ticker_id = t.id
    LEFT JOIN price_factor pf ON pf.ticker_id = t.id
    LEFT JOIN shares s ON s.ticker_id = t.id
    LEFT JOIN share_factor sf ON sf.ticker_id = t.id
    WHERE t.type IN ('common', 'adr') AND NOT t.is_benchmark
    """
)


@dataclass(frozen=True)
class Liquidity:
    ticker_id: int
    type: str
    price: float
    dollar_volume: float | None
    market_cap: float | None


async def liquidity_on(session: AsyncSession, day: date) -> list[Liquidity]:
    """Price, 50-day dollar volume and market cap of every stock with a bar on `day`."""
    rows = (await session.execute(_QUERY, {"day": day})).all()
    return [
        Liquidity(
            int(r.id),
            str(r.type),
            float(r.price),
            None if r.dollar_volume is None else float(r.dollar_volume),
            None if r.market_cap is None else float(r.market_cap),
        )
        for r in rows
    ]


def passes(stock: Liquidity, settings: AppSettings) -> bool:
    s = settings
    if s.small_cap_mode:
        price, volume, cap = (
            s.small_cap_min_price,
            s.small_cap_min_avg_dollar_volume_50d,
            s.small_cap_min_market_cap,
        )
    else:
        price, volume, cap = s.min_price, s.min_avg_dollar_volume_50d, s.min_market_cap
    if stock.type == TickerType.ADR and not s.include_adrs:
        return False
    return (
        stock.price >= price
        and stock.dollar_volume is not None
        and stock.dollar_volume >= volume
        and (stock.market_cap is None or stock.market_cap >= cap)
    )


async def liquid_tickers(session: AsyncSession, settings: AppSettings, day: date) -> set[int]:
    return {s.ticker_id for s in await liquidity_on(session, day) if passes(s, settings)}

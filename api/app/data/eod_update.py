"""End-of-day update (spec §5.5), run after each session closes:

1. Re-fetch the last few sessions for every backfilled ticker (picks up the new bar and any
   late revisions the provider made to recent ones).
2. A split we hadn't seen invalidates all earlier adjusted bars, so those tickers are marked for
   a full re-fetch.
3. Backfill anything pending: re-fetches from step 2 and listings added by the universe rebuild.
4. Refresh market caps.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_back
from app.core.logging import get_logger
from app.data.backfill import run_backfill
from app.data.bars import upsert_actions, upsert_bars
from app.data.market_cap import update_latest_market_caps
from app.models import Ticker
from app.models.ticker import BackfillStatus
from app.providers.base import PriceProvider

log = get_logger(__name__)

EOD_DELAY = timedelta(minutes=20)
REFRESH_SESSIONS = 5
BATCH_SIZE = 50


@dataclass
class EodResult:
    session: date
    tickers: int = 0
    updated: int = 0
    missing: list[str] = field(default_factory=list)
    new_splits: list[str] = field(default_factory=list)
    backfilled: int = 0
    market_caps: int = 0


async def run_eod_update(
    session: AsyncSession,
    prices: PriceProvider,
    redis: Redis,
    *,
    session_date: date,
    backfill_years: int,
    stats: dict[str, object] | None = None,
) -> EodResult:
    start = sessions_back(session_date, REFRESH_SESSIONS - 1)
    tickers = list(
        (
            await session.scalars(
                select(Ticker)
                .where(Ticker.active, Ticker.backfill_status == BackfillStatus.DONE)
                .order_by(Ticker.is_benchmark.desc(), Ticker.symbol)
            )
        ).all()
    )
    result = EodResult(session=session_date, tickers=len(tickers))
    log.info("eod.started", session=session_date, tickers=len(tickers), since=start)

    for offset in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[offset : offset + BATCH_SIZE]
        fetched = await prices.daily_history([t.symbol for t in batch], start, session_date)
        by_id = {t.id: fetched.histories[t.symbol] for t in batch if t.symbol in fetched.histories}
        result.updated += await upsert_bars(session, by_id, prices.name)
        new_splits = await upsert_actions(session, by_id, prices.name)

        split_ids = {tid for tid, _, _ in new_splits}
        for ticker in batch:
            history = by_id.get(ticker.id)
            if history is None:
                result.missing.append(ticker.symbol)
                continue
            last = history.bars[-1].date
            ticker.bars_end = last if ticker.bars_end is None else max(last, ticker.bars_end)
            if ticker.id in split_ids:
                ticker.backfill_status = BackfillStatus.PENDING
                ticker.backfill_error = "re-fetching full history after a new split"
                result.new_splits.append(ticker.symbol)
        await session.commit()

    if result.new_splits:
        log.info("eod.new_splits", symbols=result.new_splits)
    backfill = await run_backfill(
        session,
        prices,
        redis,
        today=session_date,
        end=session_date,
        years=backfill_years,
        force=False,
    )
    result.backfilled = backfill.done
    result.market_caps = await update_latest_market_caps(
        session, since=session_date - timedelta(days=10)
    )

    if stats is not None:
        stats.update(
            session=session_date.isoformat(),
            tickers=result.tickers,
            bars_updated=result.updated,
            missing=len(result.missing),
            missing_sample=result.missing[:20],
            new_splits=result.new_splits,
            backfilled=result.backfilled,
            market_caps=result.market_caps,
            finished_at=datetime.now(UTC).isoformat(),
        )
    return result

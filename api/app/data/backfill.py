"""Historical backfill (spec §5.5): daily bars for every active ticker, resumable.

Each ticker's outcome is committed batch by batch (`tickers.backfill_status`), so an interrupted
or rate-limited run picks up where it stopped. Progress is published to Redis for the admin page.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.bars import upsert_actions, upsert_bars
from app.models import Ticker
from app.models.ticker import BackfillStatus
from app.providers.base import PriceHistory, PriceProvider

log = get_logger(__name__)

PROGRESS_KEY = "backfill:progress"
PROGRESS_TTL_SECONDS = 7 * 24 * 3600
BATCH_SIZE = 25
NO_DATA_MARKERS = ("no data", "no bars")
# A first bar this long after the requested start means the stock listed inside the window.
LISTING_GRACE = timedelta(days=10)


@dataclass
class BackfillProgress:
    status: str = "idle"  # idle | running | succeeded | failed
    run_id: int | None = None
    total: int = 0
    done: int = 0
    no_data: int = 0
    failed: int = 0
    bars_written: int = 0
    start: str | None = None
    end: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    message: str | None = None
    current: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return self.done + self.no_data + self.failed


async def read_progress(redis: Redis) -> BackfillProgress:
    raw = await redis.get(PROGRESS_KEY)
    if raw is None:
        return BackfillProgress()
    return BackfillProgress(**json.loads(raw))


async def _publish(redis: Redis, progress: BackfillProgress) -> None:
    progress.updated_at = datetime.now(UTC).isoformat()
    await redis.set(PROGRESS_KEY, json.dumps(asdict(progress)), ex=PROGRESS_TTL_SECONDS)


def backfill_start(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


async def tickers_to_backfill(
    session: AsyncSession, *, symbols: Sequence[str] | None, force: bool
) -> list[Ticker]:
    query = select(Ticker).where(Ticker.active)
    if symbols:
        query = query.where(Ticker.symbol.in_([s.upper() for s in symbols]))
    if not force:
        query = query.where(
            Ticker.backfill_status.in_([BackfillStatus.PENDING, BackfillStatus.FAILED])
        )
    # Benchmarks first: the market regime and RS calculations need them before anything else.
    query = query.order_by(Ticker.is_benchmark.desc(), Ticker.symbol)
    return list((await session.scalars(query)).all())


def _apply_outcome(
    ticker: Ticker, history: PriceHistory | None, error: str | None, start: date, now: datetime
) -> str:
    if history is not None and history.bars:
        first, last = history.bars[0].date, history.bars[-1].date
        ticker.backfill_status = BackfillStatus.DONE
        ticker.backfill_error = None
        ticker.bars_start = first if ticker.bars_start is None else min(first, ticker.bars_start)
        ticker.bars_end = last if ticker.bars_end is None else max(last, ticker.bars_end)
        ticker.backfilled_at = now
        ticker.indicators_stale = True  # history (re)written: analytics must recompute it
        if first > start + LISTING_GRACE and ticker.listed_date is None:
            ticker.listed_date = first
        return BackfillStatus.DONE
    message = error or "no bars returned"
    status = (
        BackfillStatus.NO_DATA
        if any(marker in message for marker in NO_DATA_MARKERS)
        else BackfillStatus.FAILED
    )
    ticker.backfill_status = status
    ticker.backfill_error = message
    ticker.backfilled_at = now
    return status


async def run_backfill(
    session: AsyncSession,
    prices: PriceProvider,
    redis: Redis,
    *,
    today: date,
    end: date,
    years: int,
    symbols: Sequence[str] | None = None,
    force: bool = False,
    run_id: int | None = None,
    stats: dict[str, object] | None = None,
    batch_size: int = BATCH_SIZE,
) -> BackfillProgress:
    start = backfill_start(today, years)
    tickers = await tickers_to_backfill(session, symbols=symbols, force=force)
    progress = BackfillProgress(
        status="running",
        run_id=run_id,
        total=len(tickers),
        start=start.isoformat(),
        end=end.isoformat(),
        started_at=datetime.now(UTC).isoformat(),
    )
    await _publish(redis, progress)
    log.info("backfill.started", tickers=len(tickers), start=start, end=end)

    try:
        for offset in range(0, len(tickers), batch_size):
            batch = tickers[offset : offset + batch_size]
            progress.current = [t.symbol for t in batch]
            await _publish(redis, progress)

            result = await prices.daily_history([t.symbol for t in batch], start, end)
            by_id = {
                t.id: result.histories[t.symbol] for t in batch if t.symbol in result.histories
            }
            progress.bars_written += await upsert_bars(session, by_id, prices.name)
            await upsert_actions(session, by_id, prices.name)

            now = datetime.now(UTC)
            for ticker in batch:
                outcome = _apply_outcome(
                    ticker, by_id.get(ticker.id), result.errors.get(ticker.symbol), start, now
                )
                if outcome == BackfillStatus.DONE:
                    progress.done += 1
                elif outcome == BackfillStatus.NO_DATA:
                    progress.no_data += 1
                else:
                    progress.failed += 1
            await session.commit()
            log.info("backfill.batch", processed=progress.processed, total=progress.total)
    except Exception as exc:
        await session.rollback()
        progress.status = "failed"
        progress.current = []
        progress.message = (
            f"{type(exc).__name__}: {exc}. Completed tickers are kept; run the backfill again "
            "to resume."
        )
        await _publish(redis, progress)
        raise

    progress.status = "succeeded"
    progress.current = []
    await _publish(redis, progress)
    if stats is not None:
        stats.update(
            total=progress.total,
            done=progress.done,
            no_data=progress.no_data,
            failed=progress.failed,
            bars_written=progress.bars_written,
        )
    return progress

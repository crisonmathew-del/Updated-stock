"""Job entry points shared by the arq worker, the scheduler and the CLI.

Every job records a `job_runs` row and holds a Redis lock: universe, backfill and EOD update share
the `ingest` lock because they all write tickers and bars. A run that finds the lock taken is
recorded as failed with "already running" and changes nothing.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from app.core.calendar import MARKET_TZ, last_completed_session
from app.core.db import get_sessionmaker
from app.core.jobs import Trigger, job_lock, track_job
from app.core.redis import get_redis
from app.data.backfill import run_backfill
from app.data.eod_update import EOD_DELAY, run_eod_update
from app.data.quality import run_quality_checks
from app.data.universe import build_universe
from app.providers import registry
from app.providers.base import (
    FundamentalsProvider,
    PriceProvider,
    ProviderNotConfiguredError,
    ReferenceProvider,
)
from app.scanner.eod_scan import run_analytics
from app.settings import store

INGEST_LOCK = "ingest"
QUALITY_LOCK = "data_quality"


def latest_session(now: datetime | None = None) -> date:
    """The session the EOD pipeline should cover now (close + 20 minutes has passed)."""
    return last_completed_session(now or datetime.now(UTC), EOD_DELAY)


def market_today() -> date:
    return datetime.now(MARKET_TZ).date()


def _fundamentals_or_none() -> FundamentalsProvider | None:
    try:
        return registry.fundamentals_provider()
    except ProviderNotConfiguredError:
        return None


async def universe_job(
    trigger: Trigger,
    *,
    then_backfill: bool = False,
    reference: ReferenceProvider | None = None,
    fundamentals: FundamentalsProvider | None = None,
    prices: PriceProvider | None = None,
) -> dict[str, Any]:
    redis = get_redis()
    async with track_job("universe", trigger) as run, job_lock(redis, INGEST_LOCK):
        reference = reference or registry.reference_provider()
        fundamentals = fundamentals if fundamentals is not None else _fundamentals_or_none()
        try:
            async with get_sessionmaker()() as session:
                await build_universe(session, reference, fundamentals, market_today(), run.stats)
                if then_backfill:
                    settings = await store.load(session)
                    prices = prices or registry.price_provider()
                    backfill_stats: dict[str, object] = {}
                    await run_backfill(
                        session,
                        prices,
                        redis,
                        today=market_today(),
                        end=latest_session(),
                        years=settings.backfill_years,
                        run_id=run.id,
                        stats=backfill_stats,
                    )
                    run.stats["backfill"] = backfill_stats
        finally:
            await reference.aclose()
            if fundamentals is not None:
                await fundamentals.aclose()
        return run.stats


async def backfill_job(
    trigger: Trigger,
    *,
    years: int | None = None,
    symbols: Sequence[str] | None = None,
    force: bool = False,
    prices: PriceProvider | None = None,
) -> dict[str, Any]:
    redis = get_redis()
    async with track_job("backfill", trigger) as run, job_lock(redis, INGEST_LOCK):
        prices = prices or registry.price_provider()
        async with get_sessionmaker()() as session:
            settings = await store.load(session)
            await run_backfill(
                session,
                prices,
                redis,
                today=market_today(),
                end=latest_session(),
                years=years or settings.backfill_years,
                symbols=symbols,
                force=force,
                run_id=run.id,
                stats=run.stats,
            )
        await prices.aclose()
        return run.stats


async def eod_update_job(
    trigger: Trigger,
    *,
    session_date: date | None = None,
    prices: PriceProvider | None = None,
) -> dict[str, Any]:
    redis = get_redis()
    target = session_date or latest_session()
    async with track_job("eod_update", trigger) as run, job_lock(redis, INGEST_LOCK):
        prices = prices or registry.price_provider()
        async with get_sessionmaker()() as session:
            settings = await store.load(session)
            await run_eod_update(
                session,
                prices,
                redis,
                session_date=target,
                backfill_years=settings.backfill_years,
                stats=run.stats,
            )
            quality: dict[str, object] = {}
            await run_quality_checks(session, target, stats=quality)
            run.stats["quality"] = quality
            analytics: dict[str, object] = {}
            await run_analytics(session, settings, through=target, stats=analytics)
            run.stats["analytics"] = analytics
        await prices.aclose()
        return run.stats


async def data_quality_job(trigger: Trigger, *, session_date: date | None = None) -> dict[str, Any]:
    async with track_job("data_quality", trigger) as run, job_lock(get_redis(), QUALITY_LOCK):
        async with get_sessionmaker()() as session:
            await run_quality_checks(session, session_date or latest_session(), stats=run.stats)
        return run.stats


async def analytics_job(
    trigger: Trigger, *, through: date | None = None, force_full: bool = False
) -> dict[str, Any]:
    """Indicators, RS, groups, breadth and regime without fetching new prices. `force_full`
    recomputes all history (needed after changing stage or Trend Template settings)."""
    redis = get_redis()
    async with track_job("analytics", trigger) as run, job_lock(redis, INGEST_LOCK):
        async with get_sessionmaker()() as session:
            settings = await store.load(session)
            await run_analytics(
                session,
                settings,
                through=through or latest_session(),
                force_full=force_full,
                stats=run.stats,
            )
        return run.stats

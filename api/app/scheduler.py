"""Scheduler service. Run with `python -m app.scheduler`.

Owns the clock (US/Eastern) and enqueues jobs for the worker; it never does the work itself.

- EOD update: every 10 minutes from 13:00 to 23:50 on weekdays, enqueue the update for the
  latest closed session unless it already succeeded. Covers early closes (13:00) and retries
  failures without a fixed run time.
- Universe rebuild: Sundays 18:00 (spec §7.1 weekly review).
- Fundamentals: 06:00 Tuesday-Saturday for the companies that filed the previous weekday
  (spec §5.5 nightly), and a full refresh of every company on Sundays at 06:00.
- Digests: every 5 minutes, the digests job sends the daily digest (trading days, at the
  `daily_digest_time` setting) or the weekly review (Sundays 18:00) once it is due.
- First boot: if there are no tickers at all, build the universe and backfill.
The pre-market scan and the intraday sweep run in the streamer, on the live feed's clock.
"""

import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker
from app.core.heartbeat import HEARTBEAT_INTERVAL_SECONDS, beat
from app.core.lifecycle import install_stop_signals
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
from app.data.jobs import latest_session
from app.models import JobRun, Ticker

log = get_logger(__name__)


async def heartbeat() -> None:
    await beat(get_redis(), "scheduler")


async def eod_already_done(session_date: str) -> bool:
    async with get_sessionmaker()() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(JobRun)
            .where(
                JobRun.job_name == "eod_update",
                JobRun.status == "succeeded",
                JobRun.stats["session"].astext == session_date,
            )
        )
    return bool(count)


async def maybe_enqueue_eod(pool: ArqRedis) -> None:
    target = latest_session().isoformat()
    if await eod_already_done(target):
        return
    job = await pool.enqueue_job("eod_update", "schedule", target, _job_id=f"eod_update:{target}")
    if job is not None:
        log.info("scheduler.enqueued", job="eod_update", session=target)


async def enqueue_universe(pool: ArqRedis) -> None:
    week = datetime.now().strftime("%G-W%V")
    await pool.enqueue_job("universe", "schedule", _job_id=f"universe:{week}")


async def enqueue_fundamentals(pool: ArqRedis, full: bool) -> None:
    day = datetime.now().date().isoformat()
    await pool.enqueue_job("fundamentals", "schedule", full, _job_id=f"fundamentals:{day}")


async def enqueue_outcomes(pool: ArqRedis) -> None:
    day = datetime.now().date().isoformat()
    await pool.enqueue_job("outcomes", "schedule", _job_id=f"outcomes:{day}")


async def enqueue_digests(pool: ArqRedis) -> None:
    tick = datetime.now().strftime("%Y-%m-%dT%H:%M")
    await pool.enqueue_job("digests", "schedule", _job_id=f"digests:{tick}")


async def bootstrap(pool: ArqRedis) -> None:
    async with get_sessionmaker()() as session:
        tickers = await session.scalar(select(func.count()).select_from(Ticker))
    if not tickers:
        log.info("scheduler.bootstrap", reason="empty universe")
        await pool.enqueue_job("universe", "schedule", True, _job_id="universe:bootstrap")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    stop = install_stop_signals()
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    scheduler = AsyncIOScheduler(timezone=settings.market_timezone)
    scheduler.add_job(
        heartbeat,
        "interval",
        seconds=HEARTBEAT_INTERVAL_SECONDS,
        next_run_time=datetime.now(scheduler.timezone),
        id="heartbeat",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        maybe_enqueue_eod,
        "cron",
        args=[pool],
        day_of_week="mon-fri",
        hour="13-23",
        minute="*/10",
        id="eod_update",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        enqueue_universe,
        "cron",
        args=[pool],
        day_of_week="sun",
        hour=18,
        minute=0,
        id="universe",
        max_instances=1,
        coalesce=True,
    )
    for job_id, days, full in (
        ("fundamentals_nightly", "tue-sat", False),
        ("fundamentals_weekly", "sun", True),
    ):
        scheduler.add_job(
            enqueue_fundamentals,
            "cron",
            args=[pool, full],
            day_of_week=days,
            hour=6,
            minute=0,
            id=job_id,
            max_instances=1,
            coalesce=True,
        )
    scheduler.add_job(
        enqueue_outcomes,  # spec §7.1; the EOD scan also updates them when it finishes
        "cron",
        args=[pool],
        day_of_week="mon-fri",
        hour=17,
        minute=0,
        id="signal_outcomes",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        enqueue_digests,
        "cron",
        args=[pool],
        minute="*/5",
        id="digests",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("scheduler.startup", env=settings.app_env, timezone=settings.market_timezone)
    try:
        await bootstrap(pool)
    except Exception:
        log.exception("scheduler.bootstrap_failed")

    await stop.wait()
    scheduler.shutdown(wait=False)
    await pool.aclose()
    await get_redis().aclose()
    await get_engine().dispose()
    log.info("scheduler.shutdown")


if __name__ == "__main__":
    asyncio.run(main())

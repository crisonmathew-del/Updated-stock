"""Scheduler service. Run with `python -m app.scheduler`.

Owns the clock: in later phases it enqueues arq jobs on the Section 7.1 schedule (pre-market
scan, intraday sweep, EOD scan, nightly fundamentals, ...), all in US/Eastern market time.
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.core.heartbeat import HEARTBEAT_INTERVAL_SECONDS, beat
from app.core.lifecycle import install_stop_signals
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


async def heartbeat() -> None:
    await beat(get_redis(), "scheduler")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    stop = install_stop_signals()
    tz = ZoneInfo(settings.market_timezone)

    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        heartbeat,
        "interval",
        seconds=HEARTBEAT_INTERVAL_SECONDS,
        next_run_time=datetime.now(tz),
        id="heartbeat",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("scheduler.startup", env=settings.app_env, timezone=settings.market_timezone)

    await stop.wait()
    scheduler.shutdown(wait=False)
    await get_redis().aclose()
    log.info("scheduler.shutdown")


if __name__ == "__main__":
    asyncio.run(main())

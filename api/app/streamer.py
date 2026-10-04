"""Streamer service. Run with `python -m app.streamer`.

In Phase 6 this subscribes to the live feed (StreamProvider) for NEAR_PIVOT setups and
watchlists and publishes ticks to Redis. Until then it only reports that it is alive.
"""

import asyncio

from app.core.config import get_settings
from app.core.heartbeat import HEARTBEAT_INTERVAL_SECONDS, beat
from app.core.lifecycle import install_stop_signals, sleep_or_stop
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    stop = install_stop_signals()
    redis = get_redis()
    log.info("streamer.startup", env=settings.app_env, stream_provider=settings.stream_provider)

    while not stop.is_set():
        try:
            await beat(redis, "streamer")
        except Exception:
            log.exception("streamer.heartbeat_failed")
        await sleep_or_stop(stop, HEARTBEAT_INTERVAL_SECONDS)

    await redis.aclose()
    log.info("streamer.shutdown")


if __name__ == "__main__":
    asyncio.run(main())

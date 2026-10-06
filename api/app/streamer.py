"""Streamer service. Run with `python -m app.streamer`.

Runs the intraday watcher (app.intraday.watcher) on the feed STREAM_PROVIDER selects: Alpaca's
real-time trades, or a replay of a recorded session (REPLAY_FILE). With STREAM_PROVIDER=none it
only reports that it is alive. Its state ("streaming", "idle", "replay finished", or the error
that stopped the feed, with what to do) is kept in Redis for the status page; a failed feed is
retried every minute.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.heartbeat import HEARTBEAT_INTERVAL_SECONDS, beat
from app.core.lifecycle import install_stop_signals, sleep_or_stop
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
from app.intraday.service import STATUS_KEY, build_feed, make_watcher
from app.providers.replay import ReplayStream

log = get_logger(__name__)

RETRY_SECONDS = 60


async def set_status(redis: Redis, state: str, **detail: Any) -> None:
    body = {"state": state, "at": datetime.now(UTC).isoformat(), **detail}
    await redis.set(STATUS_KEY, json.dumps(body, default=str))


async def heartbeat_loop(redis: Redis, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await beat(redis, "streamer")
        except Exception:
            log.exception("streamer.heartbeat_failed")
        await sleep_or_stop(stop, HEARTBEAT_INTERVAL_SECONDS)


async def stream(redis: Redis, stop: asyncio.Event) -> None:
    config = get_settings()
    while not stop.is_set():
        try:
            feed = await build_feed(config)
        except Exception as exc:
            await set_status(redis, "error", provider=config.stream_provider, detail=str(exc))
            log.error("streamer.feed_unavailable", error=str(exc))
            await sleep_or_stop(stop, RETRY_SECONDS)
            continue
        if feed is None:
            await set_status(redis, "idle", provider="none", detail="STREAM_PROVIDER=none")
            return
        watcher = make_watcher(feed, config)
        await set_status(redis, "streaming", provider=feed.name)
        try:
            await watcher.run(stop)
        except Exception as exc:
            await set_status(redis, "error", provider=feed.name, detail=str(exc))
            log.exception("streamer.feed_failed")
            await sleep_or_stop(stop, RETRY_SECONDS)
            continue
        finally:
            if watcher.sender is not None:
                await watcher.sender.aclose()
            await feed.aclose()
        if isinstance(feed, ReplayStream):
            await set_status(redis, "replay finished", provider="replay", stats=watcher.stats)
            return


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    stop = install_stop_signals()
    redis = get_redis()
    log.info("streamer.startup", env=settings.app_env, stream_provider=settings.stream_provider)

    beating = asyncio.create_task(heartbeat_loop(redis, stop))
    try:
        await stream(redis, stop)
        await stop.wait()  # idle (or the replay ended): keep reporting that we're alive
    finally:
        stop.set()
        await beating
        await redis.aclose()
        await get_engine().dispose()
    log.info("streamer.shutdown")


if __name__ == "__main__":
    asyncio.run(main())

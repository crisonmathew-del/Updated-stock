"""Building and running the watcher for the streamer service and the CLI, plus the intraday
jobs (learning the volume curve, exporting a recording)."""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.alerts.email import email_route, sender_from_config
from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.core.heartbeat import HEARTBEAT_INTERVAL_SECONDS, beat
from app.core.jobs import Trigger, job_lock, track_job
from app.core.lifecycle import sleep_or_stop
from app.core.redis import get_redis
from app.intraday.session import session_date
from app.intraday.store import learn_volume_curve, load_bars, prev_closes
from app.intraday.watcher import Watcher
from app.providers import registry
from app.providers.base import ProviderNotConfiguredError, StreamProvider
from app.providers.replay import ReplayStream, read_recording, write_recording
from app.settings import store

STATUS_KEY = "streamer:status"


async def replay_feed(path: Path, *, speed: float, start: str | None = None) -> ReplayStream:
    """A recording as the feed, with each symbol's previous close from the database."""
    if not await asyncio.to_thread(path.exists):
        raise ProviderNotConfiguredError(
            f"Replay file {path} not found. Record a session with `make export-recording` or "
            "point REPLAY_FILE at a CSV of minute bars (symbol, ts, open, high, low, close, "
            "volume)."
        )
    bars = await asyncio.to_thread(read_recording, path)
    if not bars:
        raise ProviderNotConfiguredError(f"Replay file {path} has no bars.")
    day = session_date(bars[0].ts)
    async with get_sessionmaker()() as session:
        closes = await prev_closes(session, day, sorted({b.symbol for b in bars}))
    return ReplayStream(bars, speed=speed, prev_closes=closes, start=start)


async def build_feed(config: Settings) -> StreamProvider | None:
    """The feed STREAM_PROVIDER selects: Alpaca, a replay of REPLAY_FILE, or none."""
    if config.stream_provider == "replay":
        if not config.replay_file:
            raise ProviderNotConfiguredError(
                "STREAM_PROVIDER=replay needs REPLAY_FILE (a recording of minute bars)."
            )
        return await replay_feed(
            Path(config.replay_file), speed=config.replay_speed, start=config.replay_start
        )
    return registry.live_stream_provider(config)


def make_watcher(feed: StreamProvider, config: Settings | None = None) -> Watcher:
    config = config or get_settings()
    return Watcher(
        feed,
        sessionmaker=get_sessionmaker(),
        redis=get_redis(),
        config=config,
        sender=sender_from_config(config),
        route=email_route(config),
    )


async def set_status(state: str, **detail: Any) -> None:
    body = {"state": state, "at": datetime.now(UTC).isoformat(), **detail}
    await get_redis().set(STATUS_KEY, json.dumps(body, default=str))


async def _beat_until(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await beat(get_redis(), "streamer")
        await sleep_or_stop(stop, HEARTBEAT_INTERVAL_SECONDS)


async def run_replay(
    path: Path, *, speed: float, start: str | None = None, close: bool = True
) -> dict[str, Any]:
    """Replay a recording through the watcher in this process (the CLI's `replay`). While it
    runs it reports itself as the streamer (heartbeat and status), so pages show it as the live
    feed; don't run it alongside a streaming streamer service."""
    config = get_settings().model_copy(update={"replay_close": close})
    feed = await replay_feed(path, speed=speed, start=start)
    watcher = make_watcher(feed, config)
    stop = asyncio.Event()
    beating = asyncio.create_task(_beat_until(stop))
    await set_status("streaming", provider="replay", detail=f"Replaying {path.name}")
    try:
        await watcher.run(asyncio.Event())
        await set_status("replay finished", provider="replay", stats=watcher.stats)
    finally:
        stop.set()
        await beating
        if watcher.sender is not None:
            await watcher.sender.aclose()
        await feed.aclose()
    return {"session": session_date(feed.session).isoformat(), **watcher.stats}


async def volume_curve_job(trigger: Trigger) -> dict[str, Any]:
    """Learn the time-of-day volume curve from the stored minute bars (needs
    `volume_curve_min_sessions` complete sessions; until then the standard curve is used)."""
    async with track_job("volume_curve", trigger) as run, job_lock(get_redis(), "volume_curve"):
        async with get_sessionmaker()() as session:
            settings = await store.load(session)
            curve = await learn_volume_curve(session, settings, datetime.now(UTC).date())
        run.stats["learned"] = curve is not None
        if curve is not None:
            run.stats["share_at_noon"] = round(curve.fraction(150), 4)
        return run.stats


async def export_recording(day: date, path: Path, symbols: list[str] | None = None) -> int:
    """Write a stored session's minute bars as a recording; returns the number of bars."""
    async with get_sessionmaker()() as session:
        bars = await load_bars(session, day, symbols)
    if bars:
        await asyncio.to_thread(write_recording, bars, path)
    return len(bars)

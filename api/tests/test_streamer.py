"""The streamer service: which feed it builds and the status it reports."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from app import streamer
from app.core.calendar import MARKET_TZ
from app.core.config import Settings
from app.core.redis import get_redis
from app.intraday.service import STATUS_KEY, build_feed
from app.providers.base import MinuteBar, ProviderNotConfiguredError
from app.providers.replay import ReplayStream, write_recording


def config(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


async def test_no_provider_means_no_feed() -> None:
    assert await build_feed(config(stream_provider="none")) is None


async def test_replay_needs_a_file_and_alpaca_needs_keys(tmp_path: Path) -> None:
    with pytest.raises(ProviderNotConfiguredError, match="needs REPLAY_FILE"):
        await build_feed(config(stream_provider="replay"))
    with pytest.raises(ProviderNotConfiguredError, match="not found"):
        await build_feed(config(stream_provider="replay", replay_file=str(tmp_path / "x.csv")))


@pytest.mark.integration
@pytest.mark.usefixtures("db", "clean_redis")
async def test_a_replay_file_becomes_the_feed(tmp_path: Path) -> None:
    path = tmp_path / "day.csv.gz"
    ts = datetime(2026, 10, 2, 9, 30, tzinfo=MARKET_TZ)
    write_recording([MinuteBar("SPOT", ts, 10, 11, 9, 10.5, 1_000)], path)
    feed = await build_feed(config(stream_provider="replay", replay_file=str(path), replay_speed=0))
    assert isinstance(feed, ReplayStream)
    assert feed.symbols == ["SPOT"]


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_the_status_says_what_the_streamer_is_doing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = get_redis()
    monkeypatch.setattr(streamer, "get_settings", lambda: config(stream_provider="none"))
    await streamer.stream(redis, asyncio.Event())
    status = json.loads(await redis.get(STATUS_KEY) or "{}")
    assert (status["state"], status["detail"]) == ("idle", "STREAM_PROVIDER=none")

    monkeypatch.setattr(
        streamer, "get_settings", lambda: config(stream_provider="alpaca", alpaca_api_key_id=None)
    )
    stop = asyncio.Event()
    task = asyncio.create_task(streamer.stream(redis, stop))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if await redis.get(STATUS_KEY) and "error" in (await redis.get(STATUS_KEY) or ""):
            break
    stop.set()
    await task
    status = json.loads(await redis.get(STATUS_KEY) or "{}")
    assert status["state"] == "error"
    assert "ALPACA_API_KEY_ID" in status["detail"]

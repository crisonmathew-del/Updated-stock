from datetime import UTC, datetime, timedelta

import pytest

from app.core.heartbeat import BACKGROUND_SERVICES, HEARTBEAT_TTL_SECONDS, beat, is_alive, last_seen
from app.core.redis import get_redis

NOW = datetime(2026, 10, 5, 14, 30, tzinfo=UTC)


def test_never_seen_is_not_alive() -> None:
    assert not is_alive(None, NOW)


def test_recent_heartbeat_is_alive() -> None:
    assert is_alive(NOW - timedelta(seconds=5), NOW)
    assert is_alive(NOW - timedelta(seconds=HEARTBEAT_TTL_SECONDS), NOW)


def test_stale_heartbeat_is_not_alive() -> None:
    assert not is_alive(NOW - timedelta(seconds=HEARTBEAT_TTL_SECONDS + 1), NOW)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_beat_round_trips_through_redis() -> None:
    redis = get_redis()
    await beat(redis, "worker", now=NOW)

    seen = await last_seen(redis, BACKGROUND_SERVICES)

    assert seen == {"worker": NOW, "scheduler": None, "streamer": None}
    ttl = await redis.ttl("heartbeat:worker")
    assert 0 < ttl <= HEARTBEAT_TTL_SECONDS

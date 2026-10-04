"""Liveness heartbeats for the background services.

The worker, scheduler and streamer each write `heartbeat:<service>` to Redis every
`HEARTBEAT_INTERVAL_SECONDS` with a TTL of `HEARTBEAT_TTL_SECONDS`. The API's readiness endpoint
reads them back, so a missing or stale key means that service is down.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis

HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_TTL_SECONDS = 30
BACKGROUND_SERVICES: tuple[str, ...] = ("worker", "scheduler", "streamer")


def heartbeat_key(service: str) -> str:
    return f"heartbeat:{service}"


async def beat(redis: Redis, service: str, now: datetime | None = None) -> None:
    timestamp = (now or datetime.now(UTC)).isoformat()
    await redis.set(heartbeat_key(service), timestamp, ex=HEARTBEAT_TTL_SECONDS)


async def last_seen(redis: Redis, services: Sequence[str]) -> dict[str, datetime | None]:
    values = await redis.mget([heartbeat_key(s) for s in services])
    result: dict[str, datetime | None] = {}
    for service, raw in zip(services, values, strict=True):
        if raw is None:
            result[service] = None
        else:
            text = raw.decode() if isinstance(raw, bytes) else str(raw)
            result[service] = datetime.fromisoformat(text)
    return result


def is_alive(seen: datetime | None, now: datetime) -> bool:
    return seen is not None and (now - seen).total_seconds() <= HEARTBEAT_TTL_SECONDS

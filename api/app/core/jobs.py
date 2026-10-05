"""Job-run tracking and single-instance locks for background jobs.

    async with job_lock(redis, "backfill"), track_job("backfill", trigger="manual") as run:
        run.stats["tickers"] = 42

Every run gets a `job_runs` row: `running` at start, then `succeeded`, `failed` (with the error)
or `cancelled`, plus its duration and whatever stats the job recorded.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import update

from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.models import JobRun

log = get_logger(__name__)

Trigger = Literal["schedule", "manual", "api", "cli"]


@dataclass
class RunHandle:
    id: int
    name: str
    stats: dict[str, Any] = field(default_factory=dict)


class JobAlreadyRunningError(RuntimeError):
    pass


async def _finish(
    run_id: int, status: str, started: float, stats: dict[str, Any], error: str | None
) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(JobRun)
            .where(JobRun.id == run_id)
            .values(
                status=status,
                finished_at=datetime.now(UTC),
                duration_ms=int((time.perf_counter() - started) * 1000),
                stats=stats,
                error=error,
            )
        )
        await session.commit()


@asynccontextmanager
async def track_job(name: str, trigger: Trigger) -> AsyncIterator[RunHandle]:
    started = time.perf_counter()
    async with get_sessionmaker()() as session:
        run = JobRun(job_name=name, trigger=trigger, status="running", stats={})
        session.add(run)
        await session.commit()
        handle = RunHandle(id=run.id, name=name)

    log.info("job.started", job=name, run_id=handle.id, trigger=trigger)
    try:
        yield handle
    except asyncio.CancelledError:
        await _finish(handle.id, "cancelled", started, handle.stats, "cancelled")
        log.warning("job.cancelled", job=name, run_id=handle.id)
        raise
    except Exception as exc:
        await _finish(handle.id, "failed", started, handle.stats, f"{type(exc).__name__}: {exc}")
        log.exception("job.failed", job=name, run_id=handle.id)
        raise
    else:
        await _finish(handle.id, "succeeded", started, handle.stats, None)
        log.info("job.succeeded", job=name, run_id=handle.id, **handle.stats)


_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@asynccontextmanager
async def job_lock(redis: Redis, name: str, ttl_seconds: int = 6 * 3600) -> AsyncIterator[None]:
    """Hold `lock:<name>` for the duration of the block; raise if another run holds it.
    The TTL is a safety net so a crashed process can't hold the lock forever."""
    key = f"lock:{name}"
    token = secrets.token_hex(16)
    if not await redis.set(key, token, nx=True, ex=ttl_seconds):
        raise JobAlreadyRunningError(f"{name} is already running")
    try:
        yield
    finally:
        await redis.eval(_RELEASE, 1, key, token)  # type: ignore[misc]

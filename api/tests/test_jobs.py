import asyncio
import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import JobAlreadyRunningError, job_lock, track_job
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.models import JobRun


@pytest.mark.integration
async def test_successful_run_records_status_duration_and_stats(db: AsyncSession) -> None:
    async with track_job("example", trigger="manual") as run:
        run.stats["tickers"] = 3

    row = (await db.execute(select(JobRun))).scalar_one()
    assert (row.job_name, row.trigger, row.status) == ("example", "manual", "succeeded")
    assert row.stats == {"tickers": 3}
    assert row.finished_at is not None
    assert row.duration_ms is not None
    assert row.error is None


@pytest.mark.integration
async def test_failed_run_records_the_error_and_reraises(db: AsyncSession) -> None:
    async def failing_job() -> None:
        async with track_job("example", trigger="schedule") as run:
            run.stats["done"] = 1
            raise ValueError("provider exploded")

    with pytest.raises(ValueError, match="provider exploded"):
        await failing_job()

    row = (await db.execute(select(JobRun))).scalar_one()
    assert row.status == "failed"
    assert row.error == "ValueError: provider exploded"
    assert row.stats == {"done": 1}


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_lock_allows_one_holder_and_releases() -> None:
    redis = get_redis()
    async with job_lock(redis, "backfill"):
        with pytest.raises(JobAlreadyRunningError, match="backfill is already running"):
            async with job_lock(redis, "backfill"):
                pass
    async with job_lock(redis, "backfill"):
        pass  # released after the first block


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_rate_limiter_allows_a_burst_then_paces() -> None:
    limiter = RateLimiter(get_redis(), "test", per_second=20, burst=3)

    start = time.perf_counter()
    for _ in range(3):
        await limiter.acquire()
    burst_elapsed = time.perf_counter() - start
    for _ in range(4):
        await limiter.acquire()
    total_elapsed = time.perf_counter() - start

    assert burst_elapsed < 0.1
    # Four more tokens at 20/s need ~0.2 s of refill.
    assert 0.15 < total_elapsed < 0.6


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_rate_limiter_is_shared_between_instances() -> None:
    a = RateLimiter(get_redis(), "shared", per_second=10, burst=1)
    b = RateLimiter(get_redis(), "shared", per_second=10, burst=1)

    start = time.perf_counter()
    await asyncio.gather(a.acquire(), b.acquire(), a.acquire())

    assert time.perf_counter() - start > 0.15  # three tokens from one bucket of 1 at 10/s

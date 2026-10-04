import pytest
from arq import create_pool
from arq.worker import Worker

from app.core.config import get_settings
from app.worker import WorkerSettings


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_enqueued_job_runs_on_the_worker() -> None:
    redis_settings = WorkerSettings.redis_settings.from_dsn(get_settings().redis_url)
    pool = await create_pool(redis_settings)
    job = await pool.enqueue_job("ping")
    assert job is not None

    worker = Worker(
        functions=WorkerSettings.functions, redis_pool=pool, burst=True, poll_delay=0.01
    )
    await worker.main()

    assert await job.result(timeout=5) == "pong"
    await worker.close()
    await pool.aclose()

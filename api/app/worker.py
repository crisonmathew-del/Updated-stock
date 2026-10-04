"""arq background worker. Run with `arq app.worker.WorkerSettings`.

Jobs (EOD scan, backfill, fundamentals refresh, signal outcomes, ...) are registered here as
they are built in later phases. The heartbeat cron job proves the job loop itself is running.
"""

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.heartbeat import beat
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


async def heartbeat(ctx: dict[str, Any]) -> None:
    await beat(ctx["redis"], "worker")


async def ping(_: dict[str, Any]) -> str:
    """Trivial job used to check the queue end to end."""
    return "pong"


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    await beat(ctx["redis"], "worker")
    log.info("worker.startup", env=settings.app_env)


async def shutdown(_: dict[str, Any]) -> None:
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping]
    cron_jobs: ClassVar[list[Any]] = [
        cron(heartbeat, second={0, 10, 20, 30, 40, 50}, run_at_startup=False, timeout=5)
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    health_check_interval = 10

"""arq background worker. Run with `arq app.worker.WorkerSettings`.

The heartbeat cron job proves the job loop itself is running. Data jobs are thin wrappers over
`app.data.jobs`, which the scheduler and CLI use too.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, ClassVar, cast

from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.heartbeat import beat
from app.core.jobs import Trigger
from app.core.logging import configure_logging, get_logger
from app.data import jobs

log = get_logger(__name__)

HOUR = 3600


def _task(fn: Callable[..., Awaitable[Any]], timeout: int) -> Any:
    """arq's WorkerCoroutine protocol wants `(ctx, *args, **kwargs)`; our jobs name their args."""
    return func(cast(Any, fn), timeout=timeout)


async def heartbeat(ctx: dict[str, Any]) -> None:
    await beat(ctx["redis"], "worker")


async def ping(_: dict[str, Any]) -> str:
    """Trivial job used to check the queue end to end."""
    return "pong"


async def universe(
    _: dict[str, Any], trigger: Trigger = "api", then_backfill: bool = False
) -> dict[str, Any]:
    return await jobs.universe_job(trigger, then_backfill=then_backfill)


async def backfill(
    _: dict[str, Any],
    trigger: Trigger = "api",
    years: int | None = None,
    symbols: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    return await jobs.backfill_job(trigger, years=years, symbols=symbols, force=force)


async def eod_update(
    _: dict[str, Any], trigger: Trigger = "schedule", session_date: str | None = None
) -> dict[str, Any]:
    target = date.fromisoformat(session_date) if session_date else None
    return await jobs.eod_update_job(trigger, session_date=target)


async def data_quality(_: dict[str, Any], trigger: Trigger = "api") -> dict[str, Any]:
    return await jobs.data_quality_job(trigger)


async def analytics(
    _: dict[str, Any], trigger: Trigger = "api", force_full: bool = False
) -> dict[str, Any]:
    return await jobs.analytics_job(trigger, force_full=force_full)


async def fundamentals(
    _: dict[str, Any],
    trigger: Trigger = "api",
    full: bool = False,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    return await jobs.fundamentals_job(trigger, full=full, symbols=symbols)


async def patterns(
    _: dict[str, Any],
    trigger: Trigger = "api",
    as_of: str | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    day = date.fromisoformat(as_of) if as_of else None
    return await jobs.patterns_job(trigger, as_of=day, symbols=symbols)


async def setups(
    _: dict[str, Any], trigger: Trigger = "api", through: str | None = None
) -> dict[str, Any]:
    day = date.fromisoformat(through) if through else None
    return await jobs.setups_job(trigger, through=day)


async def outcomes(_: dict[str, Any], trigger: Trigger = "api") -> dict[str, Any]:
    return await jobs.outcomes_job(trigger)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    await beat(ctx["redis"], "worker")
    log.info("worker.startup", env=settings.app_env)


async def shutdown(_: dict[str, Any]) -> None:
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        ping,
        _task(universe, 8 * HOUR),  # includes the first full backfill on bootstrap
        _task(backfill, 8 * HOUR),
        _task(eod_update, 8 * HOUR),
        _task(data_quality, HOUR),
        _task(analytics, 4 * HOUR),
        _task(fundamentals, 8 * HOUR),  # the first full load reads every company
        _task(patterns, HOUR),
        _task(setups, 2 * HOUR),  # up to MAX_CATCH_UP sessions of detection
        _task(outcomes, HOUR),
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(heartbeat, second={0, 10, 20, 30, 40, 50}, run_at_startup=False, timeout=5)
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    health_check_interval = 10
    # Long ingest jobs share one lock, so running them in parallel only produces lock errors.
    max_jobs = 4

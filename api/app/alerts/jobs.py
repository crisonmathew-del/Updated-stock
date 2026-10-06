"""Alert jobs for the worker, the scheduler and the CLI: the EOD alerts stage (run by the EOD
update for the session just closed), due digests (a scheduler tick every 5 minutes) and
sending queued emails."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.delivery import send_alert_emails, send_due_digests
from app.alerts.email import email_route, sender_from_config
from app.alerts.eod import session_alerts
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.jobs import Trigger, job_lock, track_job
from app.core.redis import get_redis
from app.settings import store
from app.settings.schema import AppSettings

DIGEST_LOCK = "digests"


async def email_alerts(session: AsyncSession, alert_ids: Sequence[int]) -> dict[str, int]:
    if not alert_ids:
        return {}
    config = get_settings()
    sender = sender_from_config(config)
    try:
        counts = await send_alert_emails(
            session, sender, alert_ids, public_url=config.public_url, email_to=config.email_to
        )
    finally:
        if sender is not None:
            await sender.aclose()
    return dict(counts)


async def eod_alerts(session: AsyncSession, settings: AppSettings, day: date) -> dict[str, Any]:
    """The alerts stage for a session the EOD scan just processed."""
    stats, queued = await session_alerts(
        session, get_redis(), settings, day, datetime.now(UTC), email_route(get_settings())
    )
    stats["emails"] = await email_alerts(session, queued)
    return stats


async def digests_job(trigger: Trigger, *, now: datetime | None = None) -> dict[str, Any]:
    """Send the daily or weekly digest when one is due and hasn't gone out yet."""
    redis = get_redis()
    async with track_job("digests", trigger) as run, job_lock(redis, DIGEST_LOCK):
        config = get_settings()
        sender = sender_from_config(config)
        try:
            async with get_sessionmaker()() as session:
                settings = await store.load(session)
                run.stats.update(
                    await send_due_digests(
                        session,
                        redis,
                        sender,
                        settings,
                        now or datetime.now(UTC),
                        public_url=config.public_url,
                        email_to=config.email_to,
                    )
                )
        finally:
            if sender is not None:
                await sender.aclose()
        return run.stats

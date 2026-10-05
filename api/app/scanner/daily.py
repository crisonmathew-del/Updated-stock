"""The per-session stages of the EOD scan, after indicators, breadth, groups and the regime:
grades → patterns → scores → lifecycle → signals, for each session not processed yet, in
order; then signal outcomes.

The lifecycle must see every session (a breakout is judged on its own day's volume), so
sessions missed since the last run are caught up one by one, at most MAX_CATCH_UP (older ones
are skipped with a warning). Re-running the latest processed session re-evaluates it from the
snapshot taken before it; a session older than that is never re-scored (signals are a log of
what the system said at the time), and only its patterns and grades are refreshed.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_between
from app.core.logging import get_logger
from app.models import IndicatorDaily, ScanProgress
from app.scanner.detection import run_detection
from app.scanner.outcomes import update_outcomes
from app.scanner.setups import run_setups
from app.settings.schema import AppSettings

log = get_logger(__name__)

STAGE = "setups"
MAX_CATCH_UP = 10


async def latest_analytics_date(session: AsyncSession, through: date) -> date | None:
    return await session.scalar(
        select(func.max(IndicatorDaily.date)).where(IndicatorDaily.date <= through)
    )


async def setups_through(session: AsyncSession) -> date | None:
    progress = await session.get(ScanProgress, STAGE)
    return None if progress is None else progress.through


async def _mark(session: AsyncSession, day: date, stats: dict[str, Any]) -> None:
    stmt = insert(ScanProgress).values(stage=STAGE, through=day, stats=stats)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["stage"],
            set_={"through": stmt.excluded.through, "stats": stmt.excluded.stats},
        )
    )
    await session.commit()


def pending_sessions(last: date | None, latest: date) -> tuple[list[date], int]:
    """Sessions to evaluate, oldest first, and how many missed ones were skipped."""
    if last is None or last >= latest:
        return [latest], 0
    missed = sessions_between(last + timedelta(days=1), latest) or [latest]
    keep = missed[-MAX_CATCH_UP:]
    return keep, len(missed) - len(keep)


async def run_daily(session: AsyncSession, settings: AppSettings, through: date) -> dict[str, Any]:
    latest = await latest_analytics_date(session, through)
    if latest is None:
        return {}
    last = await setups_through(session)
    if last is not None and last > latest:
        # An older session: refresh its grades and patterns only.
        detection, _ = await run_detection(session, settings, latest)
        log.info("setups.skipped_older_session", as_of=latest, processed_through=last)
        return {**detection, "setups": {"skipped": f"already processed through {last}"}}

    days, skipped = pending_sessions(last, latest)
    if skipped:
        log.warning("setups.catch_up_capped", skipped=skipped, from_=days[0], to=latest)
    stats: dict[str, Any] = {}
    setups: dict[str, Any] = {}
    for day in days:
        stats, run = await run_detection(session, settings, day)
        if not run.closes:
            log.warning("setups.no_bars", as_of=day)  # nothing scanned: leave setups alone
            continue
        setups = await run_setups(session, settings, day, run)
        await _mark(session, day, setups)
    outcomes = await update_outcomes(session, latest)
    return {
        **stats,
        "setups": {**setups, "sessions": [d.isoformat() for d in days], "skipped": skipped},
        "outcomes": outcomes,
    }

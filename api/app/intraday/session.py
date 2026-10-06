"""Where a moment falls in the US trading day (all intraday logic runs on event timestamps,
never the wall clock, so replay and live behave the same)."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache

from app.core.calendar import MARKET_TZ, is_session, session_close

OPEN = time(9, 30)
PREMARKET_START = time(4, 0)
REGULAR_MINUTES = 390


class Phase(StrEnum):
    CLOSED = "closed"  # not a session, or overnight
    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"


@dataclass(frozen=True, slots=True)
class SessionTimes:
    day: date
    open: datetime  # US/Eastern
    close: datetime  # 16:00, or 13:00 on early-close days

    @property
    def minutes(self) -> int:
        return int((self.close - self.open).total_seconds() // 60)


@lru_cache(maxsize=64)
def session_times(day: date) -> SessionTimes | None:
    if not is_session(day):
        return None
    return SessionTimes(day, datetime.combine(day, OPEN, MARKET_TZ), session_close(day))


def eastern(ts: datetime) -> datetime:
    return ts.astimezone(MARKET_TZ)


def phase(ts: datetime) -> Phase:
    local = eastern(ts)
    times = session_times(local.date())
    if times is None:
        return Phase.CLOSED
    if local < datetime.combine(times.day, PREMARKET_START, MARKET_TZ):
        return Phase.CLOSED
    if local < times.open:
        return Phase.PREMARKET
    if local < times.close:
        return Phase.REGULAR
    return Phase.AFTER_HOURS


def minutes_elapsed(ts: datetime) -> float:
    """Minutes since the regular open, on a 390-minute scale (an early-close session is
    stretched so the volume curve still lines up), clamped to [0, 390]."""
    local = eastern(ts)
    times = session_times(local.date())
    if times is None:
        return 0.0
    raw = (local - times.open).total_seconds() / 60
    scaled = raw * REGULAR_MINUTES / times.minutes
    return min(float(REGULAR_MINUTES), max(0.0, scaled))


def minute_start(ts: datetime) -> datetime:
    """The start of the minute containing `ts` (keeps its timezone)."""
    return ts.replace(second=0, microsecond=0)


def session_date(ts: datetime) -> date:
    return eastern(ts).date()


def at(day: date, hhmm: str) -> datetime:
    """`day` at an "HH:MM" US/Eastern time."""
    hours, minutes = (int(p) for p in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), MARKET_TZ)


def in_window(ts: datetime, start: str, end: str) -> bool:
    """Whether `ts` (any zone) falls in [start, end) US/Eastern; a window may wrap midnight
    ("22:00"-"07:00")."""
    local = eastern(ts)
    begin, finish = at(local.date(), start), at(local.date(), end)
    if begin <= finish:
        return begin <= local < finish
    return local >= begin or local < finish


def next_minute(ts: datetime) -> datetime:
    return minute_start(ts) + timedelta(minutes=1)

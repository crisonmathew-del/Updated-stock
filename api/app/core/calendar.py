"""NYSE trading calendar: sessions, holidays and early closes.

Backed by `exchange_calendars` (XNYS). All public functions take and return plain `date`s or
timezone-aware `datetime`s in US/Eastern, so pandas stays inside this module.
"""

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

MARKET_TZ = ZoneInfo("America/New_York")
CALENDAR_START = "1995-01-01"


@lru_cache
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS", start=CALENDAR_START)


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)


def _date(value: object) -> date:
    """exchange_calendars is untyped; normalise its Timestamps to `date`."""
    return pd.Timestamp(str(value)).date()


def is_session(d: date) -> bool:
    return bool(_xnys().is_session(_ts(d)))


def sessions_between(start: date, end: date) -> list[date]:
    """Trading sessions from `start` to `end`, inclusive."""
    if end < start:
        return []
    return [ts.date() for ts in _xnys().sessions_in_range(_ts(start), _ts(end))]


def session_close(d: date) -> datetime:
    """The session's closing time in US/Eastern (13:00 on early-close days, else 16:00)."""
    close: pd.Timestamp = _xnys().session_close(_ts(d))
    return close.to_pydatetime().astimezone(MARKET_TZ)


def previous_session(d: date) -> date:
    """The last session strictly before `d`."""
    cal = _xnys()
    session = cal.date_to_session(_ts(d), direction="previous")
    if _date(session) == d:
        session = cal.previous_session(session)
    return _date(session)


def sessions_back(d: date, count: int) -> date:
    """The session `count` sessions before the session on or before `d`."""
    cal = _xnys()
    session = cal.date_to_session(_ts(d), direction="previous")
    return _date(cal.session_offset(session, -count))


def last_completed_session(now: datetime, delay: timedelta = timedelta(0)) -> date:
    """The most recent session whose close (plus `delay`) is at or before `now`."""
    today = now.astimezone(MARKET_TZ).date()
    cal = _xnys()
    candidate = _date(cal.date_to_session(_ts(today), direction="previous"))
    if session_close(candidate) + delay > now:
        candidate = previous_session(candidate)
    return candidate

from datetime import date, datetime, time, timedelta

import pytest

from app.core.calendar import (
    MARKET_TZ,
    is_session,
    last_completed_session,
    previous_session,
    session_close,
    sessions_back,
    sessions_between,
)


def et(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), tzinfo=MARKET_TZ)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2025, 7, 4), False),  # Independence Day
        (date(2025, 11, 27), False),  # Thanksgiving
        (date(2025, 12, 25), False),  # Christmas
        (date(2026, 1, 1), False),  # New Year's Day
        (date(2026, 1, 19), False),  # Martin Luther King Jr. Day
        (date(2026, 4, 3), False),  # Good Friday (Easter is 5 April 2026)
        (date(2026, 10, 3), False),  # Saturday
        (date(2026, 10, 2), True),  # ordinary Friday
        (date(2025, 11, 28), True),  # day after Thanksgiving: open, closes early
    ],
)
def test_holidays_and_weekends(day: date, expected: bool) -> None:
    assert is_session(day) is expected


@pytest.mark.parametrize(
    ("day", "close"),
    [
        (date(2026, 10, 2), time(16, 0)),
        (date(2025, 7, 3), time(13, 0)),  # eve of Independence Day
        (date(2025, 11, 28), time(13, 0)),  # day after Thanksgiving
        (date(2025, 12, 24), time(13, 0)),  # Christmas Eve
        (date(2026, 3, 9), time(16, 0)),  # first session after the DST change
        (date(2026, 1, 5), time(16, 0)),  # winter time
    ],
)
def test_session_close_in_eastern_time(day: date, close: time) -> None:
    result = session_close(day)
    assert result.tzinfo is not None
    assert result.astimezone(MARKET_TZ).time() == close
    assert result.astimezone(MARKET_TZ).date() == day


def test_sessions_between_skips_holidays_inclusive_of_both_ends() -> None:
    assert sessions_between(date(2025, 12, 22), date(2026, 1, 2)) == [
        date(2025, 12, 22),
        date(2025, 12, 23),
        date(2025, 12, 24),
        date(2025, 12, 26),
        date(2025, 12, 29),
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 2),
    ]
    assert sessions_between(date(2026, 1, 3), date(2026, 1, 2)) == []


def test_previous_session_and_offsets() -> None:
    assert previous_session(date(2025, 12, 26)) == date(2025, 12, 24)
    assert previous_session(date(2025, 12, 27)) == date(2025, 12, 26)  # Saturday
    assert previous_session(date(2026, 1, 20)) == date(2026, 1, 16)  # after MLK day
    assert sessions_back(date(2026, 1, 2), 1) == date(2025, 12, 31)
    assert sessions_back(date(2026, 1, 3), 0) == date(2026, 1, 2)  # Saturday → Friday


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (et(date(2025, 11, 28), 13, 10), date(2025, 11, 26)),  # early close + 20 min not reached
        (et(date(2025, 11, 28), 13, 25), date(2025, 11, 28)),  # early close + 20 min passed
        (et(date(2026, 10, 2), 16, 19), date(2026, 10, 1)),
        (et(date(2026, 10, 2), 16, 20), date(2026, 10, 2)),
        (et(date(2026, 10, 3), 10, 0), date(2026, 10, 2)),  # Saturday
        (et(date(2026, 10, 5), 9, 0), date(2026, 10, 2)),  # Monday before the open
    ],
)
def test_last_completed_session_respects_close_and_delay(now: datetime, expected: date) -> None:
    assert last_completed_session(now, delay=timedelta(minutes=20)) == expected

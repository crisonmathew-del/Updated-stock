"""The session clock and the time-of-day volume projection, checked by hand."""

from datetime import UTC, date, datetime
from itertools import pairwise

import polars as pl
import pytest

from app.core.calendar import MARKET_TZ
from app.intraday.session import Phase, in_window, minutes_elapsed, phase, session_times
from app.intraday.volume import AUCTION_SHARE, STANDARD_CURVE, learn_curve

FRIDAY = date(2026, 10, 2)
HALF_DAY = date(2026, 11, 27)  # the Friday after Thanksgiving closes at 13:00


def et(day: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=MARKET_TZ)


def test_phases_of_the_day() -> None:
    assert phase(et(FRIDAY, 3, 0)) is Phase.CLOSED
    assert phase(et(FRIDAY, 8, 0)) is Phase.PREMARKET
    assert phase(et(FRIDAY, 9, 30)) is Phase.REGULAR
    assert phase(et(FRIDAY, 15, 59, 59)) is Phase.REGULAR
    assert phase(et(FRIDAY, 16, 0)) is Phase.AFTER_HOURS
    assert phase(et(date(2026, 10, 3), 11, 0)) is Phase.CLOSED  # Saturday
    # The same moment in UTC: 13:45 UTC is 09:45 EDT.
    assert phase(datetime(2026, 10, 2, 13, 45, tzinfo=UTC)) is Phase.REGULAR


def test_minutes_elapsed_stretches_early_closes() -> None:
    assert minutes_elapsed(et(FRIDAY, 10, 15)) == 45
    assert minutes_elapsed(et(FRIDAY, 9, 0)) == 0  # before the open
    assert minutes_elapsed(et(FRIDAY, 17, 0)) == 390  # after the close
    times = session_times(HALF_DAY)
    assert times is not None
    assert times.minutes == 210
    # 11:15 is 105 of 210 minutes: half the session, 195 on the 390-minute scale.
    assert minutes_elapsed(et(HALF_DAY, 11, 15)) == 195


def test_quiet_window_can_wrap_midnight() -> None:
    assert in_window(et(FRIDAY, 23, 0), "22:00", "07:00")
    assert in_window(et(FRIDAY, 6, 59), "22:00", "07:00")
    assert not in_window(et(FRIDAY, 12, 0), "22:00", "07:00")
    assert in_window(et(FRIDAY, 12, 0), "09:00", "17:00")
    assert not in_window(et(FRIDAY, 17, 0), "09:00", "17:00")


def test_standard_curve_values() -> None:
    curve = STANDARD_CURVE
    assert curve.fraction(0) == 0
    assert curve.fraction(30) == pytest.approx(0.133)
    # 45 min: halfway from 30 (0.133) to 60 (0.227) = 0.133 + 0.094 / 2 = 0.180.
    assert curve.fraction(45) == pytest.approx(0.180)
    # 45.5 min: 0.133 + 15.5 / 30 × 0.094 = 0.181567.
    assert curve.fraction(45.5) == pytest.approx(0.1815667, abs=1e-6)
    assert curve.fraction(390) == pytest.approx(0.920)
    assert pytest.approx(0.08) == AUCTION_SHARE
    steps = curve.cumulative
    assert len(steps) == 391
    assert all(b >= a for a, b in pairwise(steps))


def test_projection_uses_the_curve_not_a_straight_line() -> None:
    # 1.8M shares by 10:15 (45 min, 18% of a normal day) projects to 10M for the day.
    assert STANDARD_CURVE.project(1_800_000, et(FRIDAY, 10, 15)) == pytest.approx(10_000_000)
    # A straight line would say 1.8M × 390 / 45 = 15.6M.
    assert STANDARD_CURVE.project(1_000, et(FRIDAY, 9, 0)) is None  # before the open
    # Half-day 11:15 sits at 195 on the 390 scale: 0.477 + 15/30 × 0.047 = 0.5005.
    assert STANDARD_CURVE.project(5_005_000, et(HALF_DAY, 11, 15)) == pytest.approx(10_000_000)


def minute_frame(sessions: int, tickers: int, minutes: int = 390) -> pl.DataFrame:
    rows = [
        {"ticker_id": t, "session": f"s{s}", "minute": m, "volume": 100}
        for s in range(sessions)
        for t in range(tickers)
        for m in range(minutes)
    ]
    return pl.DataFrame(rows)


def test_learned_curve_needs_enough_complete_sessions() -> None:
    flat = minute_frame(sessions=3, tickers=2)
    learned = learn_curve(flat, min_sessions=3)
    assert learned is not None
    assert learned.source == "learned from 3 sessions"
    # Constant volume per minute: the share grows linearly, scaled to 0.92 by the last minute.
    assert learned.fraction(195) == pytest.approx(0.46)
    assert learned.fraction(390) == pytest.approx(0.92)
    assert learn_curve(flat, min_sessions=4) is None
    # Sessions with gaps (under 95% of the minutes) don't count.
    gappy = minute_frame(sessions=5, tickers=1, minutes=300)
    assert learn_curve(gappy, min_sessions=1) is None
    assert learn_curve(pl.DataFrame(), min_sessions=1) is None

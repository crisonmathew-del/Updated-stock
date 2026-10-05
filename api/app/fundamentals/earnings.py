"""Earnings dates: the next release estimated from the company's own history.

SEC has no forward calendar, so until a paid provider supplies confirmed dates the next release
is estimated as 52 weeks after a past release (same weekday, same quarter last year). The
estimate is labelled as such everywhere it is shown.
"""

from collections.abc import Iterable
from datetime import date, timedelta

ONE_YEAR = timedelta(days=364)  # 52 weeks keeps the weekday
QUARTER = timedelta(days=91)
# The next release is expected between these gaps after the latest one.
MIN_GAP = timedelta(days=45)
MAX_GAP = timedelta(days=135)
# A release this late is treated as imminent; later than that, the cadence is broken (acquired,
# delisted, late filer) and no estimate is better than a confidently wrong one.
OVERDUE_GRACE = timedelta(days=30)


def _weekday(day: date) -> date:
    """Move a weekend date to the following Monday."""
    return day + timedelta(days=(7 - day.weekday()) % 7) if day.weekday() >= 5 else day


def estimate_next_release(reported: Iterable[date], as_of: date) -> date | None:
    """The next results date after `as_of`, using only releases on or before `as_of`.

    Expected date: the release from 52 weeks earlier that falls 45-135 days after the latest
    release (same quarter last year), else the latest release plus 91 days. If that date has
    already passed without a release, the release is overdue and taken as the next weekday,
    for up to 30 days; after that there is no estimate."""
    past = sorted({d for d in reported if d <= as_of})
    if not past:
        return None
    last = past[-1]
    analogs = [d + ONE_YEAR for d in past if last + MIN_GAP <= d + ONE_YEAR <= last + MAX_GAP]
    expected = _weekday(min(analogs) if analogs else last + QUARTER)
    if expected > as_of:
        return expected
    if as_of - expected <= OVERDUE_GRACE:
        return _weekday(as_of + timedelta(days=1))
    return None

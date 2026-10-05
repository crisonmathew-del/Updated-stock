"""Setup lifecycle transitions, one table row per rule, worked with the default settings:
near pivot within 3%, breakout volume ≥ 140% of the 50-day average, close ≥ 67% up the
day's range, buy zone 5% above the pivot, failed breakout within 3 sessions."""

from dataclasses import replace
from datetime import date

import pytest

from app.scoring.lifecycle import Session, SetupFacts, State, next_state
from app.settings.schema import DEFAULTS

PIVOT = 100.0
BASE = SetupFacts(state=State.BASING, kind="base", pivot=PIVOT, base_low=90.0, pattern_current=True)
AFTER = replace(BASE, state=State.BREAKOUT, stop=96.0, sessions_since_breakout=2)
DAY = date(2026, 10, 5)


def day(
    close: float, *, high: float | None = None, low: float | None = None, volume: float = 1000.0
) -> Session:
    # Default range: close at the top of a 2-point range (100% up the day).
    return Session(
        DAY,
        close=close,
        high=close if high is None else high,
        low=close - 2 if low is None else low,
        volume=volume,
        avg_volume_50=1000.0,
    )


CASES = [
    # (name, facts, session, expected state, reason)
    ("far below: basing", BASE, day(96.0), State.BASING, "Close 4.0% below the pivot 100.00."),
    ("within 3%: near", BASE, day(97.5), State.NEAR_PIVOT, "Close 2.5% below the pivot 100.00."),
    ("exactly 3%: near", BASE, day(97.0), State.NEAR_PIVOT, "Close 3.0% below the pivot 100.00."),
    ("back down: basing", replace(BASE, state=State.NEAR_PIVOT), day(96.9), State.BASING, None),
    (
        # 102 on 1500 (150%), range 99-102.5: (102 - 99) / 3.5 = 86% up the range.
        "confirmed breakout",
        BASE,
        day(102.0, high=102.5, low=99.0, volume=1500.0),
        State.BREAKOUT,
        "Closed 2.0% above the pivot 100.00 on 150% of average volume, 86% up the day's range.",
    ),
    (
        "breakout at the minimums",  # exactly 140% and exactly 67% up the range
        BASE,
        day(100.67, high=101.0, low=100.0, volume=1400.0),
        State.BREAKOUT,
        None,
    ),
    (
        "light volume: rejected",
        BASE,
        day(101.0, volume=1300.0),
        State.NEAR_PIVOT,
        "Closed above the pivot 100.00, but volume was 130% of average (needs 140%).",
    ),
    (
        # range 99-103, close 101: 50% up the range.
        "weak close: rejected",
        BASE,
        day(101.0, high=103.0, low=99.0, volume=2000.0),
        State.NEAR_PIVOT,
        "Closed above the pivot 100.00, but it closed 50% up the day's range (needs 67%).",
    ),
    (
        "both problems",
        BASE,
        day(101.0, high=103.0, low=99.0, volume=900.0),
        State.NEAR_PIVOT,
        "Closed above the pivot 100.00, but volume was 90% of average (needs 140%) and it "
        "closed 50% up the day's range (needs 67%).",
    ),
    (
        "gap through the zone: extended",
        BASE,
        day(106.0, volume=3000.0),
        State.EXTENDED,
        "Broke out on 300% of average volume, 100% up the day's range, but closed 6.0% above "
        "the pivot 100.00: beyond the buy zone, don't chase.",
    ),
    (
        "close below the base low",
        BASE,
        day(89.5),
        State.INVALIDATED,
        "Closed below the base low 90.00 (89.50).",
    ),
    (
        "detector reports failure",
        replace(BASE, pattern_failed=True, base_low=None),
        day(97.0),
        State.INVALIDATED,
        "The pattern failed: it broke below its low.",
    ),
    (
        "base stops matching",
        replace(BASE, pattern_current=False),
        day(97.0),
        State.INVALIDATED,
        "The base no longer meets its rules.",
    ),
    (
        "holding in the zone",
        AFTER,
        day(103.0),
        State.BREAKOUT,
        "Holding +3.0% from the pivot 100.00.",
    ),
    (
        "runs past the zone",
        AFTER,
        day(105.5),
        State.EXTENDED,
        "Closed 5.5% above the pivot 100.00: extended beyond the 5% buy zone, don't chase.",
    ),
    (
        "stays extended",
        replace(AFTER, state=State.EXTENDED, sessions_since_breakout=8),
        day(110.0),
        State.EXTENDED,
        "Still 10.0% above the pivot.",
    ),
    (
        "extended pulls back into the zone: stays extended",
        replace(AFTER, state=State.EXTENDED, sessions_since_breakout=8),
        day(102.0),
        State.EXTENDED,
        "Holding +2.0% from the pivot 100.00.",
    ),
    (
        "stop hit intraday",
        AFTER,
        day(99.0, low=95.9),
        State.FAILED,
        "Stop 96.00 hit (low 95.90).",
    ),
    (
        "back below the pivot within 3 sessions",
        replace(AFTER, sessions_since_breakout=3),
        day(99.5, low=98.0),
        State.FAILED,
        "Closed back below the pivot 100.00 (99.50) 3 session(s) after the breakout.",
    ),
    (
        "below the pivot after 3 sessions: holds",
        replace(AFTER, sessions_since_breakout=4),
        day(99.5, low=98.0),
        State.BREAKOUT,
        "Holding -0.5% from the pivot 100.00.",
    ),
    (
        "failed stays failed",
        replace(AFTER, state=State.FAILED),
        day(120.0),
        State.FAILED,
        "Closed.",
    ),
    (
        "invalidated stays invalidated",
        replace(BASE, state=State.INVALIDATED),
        day(102.0, volume=5000.0),
        State.INVALIDATED,
        "Closed.",
    ),
]


@pytest.mark.parametrize(
    ("facts", "session", "expected", "reason"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_transitions(
    facts: SetupFacts, session: Session, expected: State, reason: str | None
) -> None:
    result = next_state(facts, session, DEFAULTS)
    assert result.state == expected
    if reason is not None:
        assert result.reason == reason


def test_breakout_day_flags() -> None:
    confirmed = next_state(BASE, day(102.0, volume=1500.0), DEFAULTS)
    assert (confirmed.breakout_today, confirmed.rejected_breakout) == (True, None)
    gap = next_state(BASE, day(106.0, volume=1500.0), DEFAULTS)
    assert (gap.state, gap.breakout_today) == (State.EXTENDED, True)
    rejected = next_state(BASE, day(101.0, volume=1000.0), DEFAULTS)
    assert not rejected.breakout_today
    assert rejected.rejected_breakout == rejected.reason
    # Already past the breakout: a strong day above the pivot isn't a new breakout.
    holding = next_state(AFTER, day(102.0, volume=5000.0), DEFAULTS)
    assert not holding.breakout_today


def test_no_volume_average_cannot_confirm() -> None:
    session = replace(day(102.0, volume=5000.0), avg_volume_50=None)
    result = next_state(BASE, session, DEFAULTS)
    assert result.state == State.NEAR_PIVOT
    assert result.rejected_breakout == (
        "Closed above the pivot 100.00, but no 50-day volume average yet."
    )


def test_a_flat_day_counts_as_closing_at_the_top() -> None:
    result = next_state(BASE, day(101.0, high=101.0, low=101.0, volume=1500.0), DEFAULTS)
    assert result.state == State.BREAKOUT


def test_settings_move_the_lines() -> None:
    wider = DEFAULTS.model_copy(update={"near_pivot_pct": 5.0, "buy_zone_max_pct_above_pivot": 8})
    assert next_state(BASE, day(96.0), wider).state == State.NEAR_PIVOT
    assert next_state(BASE, day(106.0, volume=1500.0), wider).state == State.BREAKOUT
    looser = DEFAULTS.model_copy(update={"breakout_volume_min_pct_of_avg": 120.0})
    assert next_state(BASE, day(101.0, volume=1300.0), looser).state == State.BREAKOUT


def test_watch_setups_follow_leadership() -> None:
    watch = SetupFacts(state=State.WATCH, kind="watch", trend_leader=True, leader_detail="RS 91.")
    kept = next_state(watch, day(50.0), DEFAULTS)
    assert (kept.state, kept.reason) == (State.WATCH, "Trend leader without a base. RS 91.")
    lost = next_state(replace(watch, trend_leader=False, leader_detail="RS 64."), day(50), DEFAULTS)
    assert (lost.state, lost.reason) == (State.INVALIDATED, "No longer a trend leader. RS 64.")


def test_a_new_setup_starts_from_today() -> None:
    fresh = replace(BASE, state=None)
    assert next_state(fresh, day(98.0), DEFAULTS).state == State.NEAR_PIVOT
    assert next_state(fresh, day(102.0, volume=1500.0), DEFAULTS).state == State.BREAKOUT

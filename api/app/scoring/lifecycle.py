"""Setup lifecycle (spec §6.10): one transition per session, each with a reason.

    WATCH ──► BASING ──► NEAR_PIVOT ──► BREAKOUT ──► EXTENDED
                 │            │             │
                 └────────────┴──► INVALIDATED   └──► FAILED

- WATCH: a trend leader (Trend Template pass, RS ≥ rs_rating_min) without a base. Leaving
  leadership invalidates it; a new base moves it to BASING (same setup).
- BASING / NEAR_PIVOT: a base (or an earnings gap) is current; NEAR_PIVOT once the close is
  within `near_pivot_pct` below the pivot.
- BREAKOUT: a close above the pivot confirmed by volume (at least
  `breakout_volume_min_pct_of_avg` of the 50-day average) and a close in the top part of the
  day's range (`breakout_close_range_min_pct`). Judged at the close (live "provisional"
  breakouts arrive with real-time data in Phase 6). An unconfirmed close above the pivot
  stays NEAR_PIVOT and is reported as a rejected breakout.
- EXTENDED: more than `buy_zone_max_pct_above_pivot` above the pivot after (or on) a
  confirmed breakout: don't chase. There is no way back to BREAKOUT.
- FAILED (after a breakout): the stop is hit (the day's low), or a close back below the pivot
  within `failed_breakout_sessions` of the breakout.
- INVALIDATED (before a breakout): the base breaks down (close below its low) or stops
  meeting its rules.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.settings.schema import AppSettings


class State(StrEnum):
    WATCH = "watch"
    BASING = "basing"
    NEAR_PIVOT = "near_pivot"
    BREAKOUT = "breakout"
    EXTENDED = "extended"
    FAILED = "failed"
    INVALIDATED = "invalidated"


LABELS = {
    State.WATCH: "Watch",
    State.BASING: "Basing",
    State.NEAR_PIVOT: "Near pivot",
    State.BREAKOUT: "Breakout",
    State.EXTENDED: "Extended",
    State.FAILED: "Failed",
    State.INVALIDATED: "Invalidated",
}
TERMINAL = frozenset({State.FAILED, State.INVALIDATED})
POST_BREAKOUT = frozenset({State.BREAKOUT, State.EXTENDED})
EPS = 1e-9  # thresholds are inclusive; ignore float noise such as 97 / 100 - 1 = -0.0300...03


@dataclass(frozen=True)
class Session:
    """The session being judged. `avg_volume_50` is the average of the 50 sessions before."""

    date: date
    close: float
    high: float
    low: float
    volume: float
    avg_volume_50: float | None


@dataclass(frozen=True)
class SetupFacts:
    state: State | None  # yesterday's state; None for a new setup
    kind: str  # watch | base | episodic_pivot
    pivot: float | None = None
    base_low: float | None = None
    pattern_current: bool = False  # the base (or gap) is detected today
    pattern_failed: bool = False  # the detector reports it failed (e.g. gap low undercut)
    stop: float | None = None  # the trade plan's stop, frozen at the breakout
    sessions_since_breakout: int | None = None  # 0 on the breakout day
    trend_leader: bool = False
    leader_detail: str = ""


@dataclass(frozen=True)
class Transition:
    state: State
    reason: str
    breakout_today: bool = False
    rejected_breakout: str | None = None  # why a close above the pivot wasn't a breakout


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


def next_state(facts: SetupFacts, day: Session, settings: AppSettings) -> Transition:
    s = settings
    previous = facts.state
    if previous in TERMINAL:
        return Transition(previous, "Closed.")

    if facts.kind == "watch":
        if facts.trend_leader:
            return Transition(
                State.WATCH, f"Trend leader without a base. {facts.leader_detail}".strip()
            )
        return Transition(
            State.INVALIDATED, f"No longer a trend leader. {facts.leader_detail}".strip()
        )

    pivot = facts.pivot
    assert pivot is not None, "a base setup needs a pivot"
    zone_top = pivot * (1 + s.buy_zone_max_pct_above_pivot / 100)

    if previous in POST_BREAKOUT:
        if facts.stop is not None and day.low <= facts.stop:
            return Transition(State.FAILED, f"Stop {facts.stop:.2f} hit (low {day.low:.2f}).")
        since = facts.sessions_since_breakout
        if since is not None and since <= s.failed_breakout_sessions and day.close < pivot:
            return Transition(
                State.FAILED,
                f"Closed back below the pivot {pivot:.2f} ({day.close:.2f}) {since} session(s) "
                "after the breakout.",
            )
        if day.close > zone_top:
            if previous == State.EXTENDED:
                return Transition(
                    State.EXTENDED, f"Still {_pct(day.close, pivot):.1f}% above the pivot."
                )
            return Transition(
                State.EXTENDED,
                f"Closed {_pct(day.close, pivot):.1f}% above the pivot {pivot:.2f}: extended "
                f"beyond the {s.buy_zone_max_pct_above_pivot:g}% buy zone, don't chase.",
            )
        assert previous is not None
        return Transition(
            previous, f"Holding {_pct(day.close, pivot):+.1f}% from the pivot {pivot:.2f}."
        )

    if facts.base_low is not None and day.close < facts.base_low:
        return Transition(
            State.INVALIDATED, f"Closed below the base low {facts.base_low:.2f} ({day.close:.2f})."
        )
    if facts.pattern_failed:
        return Transition(State.INVALIDATED, "The pattern failed: it broke below its low.")
    if not facts.pattern_current:
        return Transition(State.INVALIDATED, "The base no longer meets its rules.")

    if day.close > pivot:
        span = day.high - day.low
        place = (day.close - day.low) / span * 100 if span > 0 else 100.0
        volume_pct = day.volume / day.avg_volume_50 * 100 if day.avg_volume_50 else None
        problems = []
        if volume_pct is None:
            problems.append("no 50-day volume average yet")
        elif volume_pct < s.breakout_volume_min_pct_of_avg - EPS:
            problems.append(
                f"volume was {volume_pct:.0f}% of average (needs "
                f"{s.breakout_volume_min_pct_of_avg:g}%)"
            )
        if place < s.breakout_close_range_min_pct - EPS:
            problems.append(
                f"it closed {place:.0f}% up the day's range (needs "
                f"{s.breakout_close_range_min_pct:g}%)"
            )
        if problems:
            rejected = f"Closed above the pivot {pivot:.2f}, but " + " and ".join(problems) + "."
            return Transition(State.NEAR_PIVOT, rejected, rejected_breakout=rejected)
        assert volume_pct is not None
        evidence = f"on {volume_pct:.0f}% of average volume, {place:.0f}% up the day's range"
        if day.close > zone_top:
            return Transition(
                State.EXTENDED,
                f"Broke out {evidence}, but closed {_pct(day.close, pivot):.1f}% above the pivot "
                f"{pivot:.2f}: beyond the buy zone, don't chase.",
                breakout_today=True,
            )
        return Transition(
            State.BREAKOUT,
            f"Closed {_pct(day.close, pivot):.1f}% above the pivot {pivot:.2f} {evidence}.",
            breakout_today=True,
        )

    below = -_pct(day.close, pivot)
    if below <= s.near_pivot_pct + EPS:
        return Transition(State.NEAR_PIVOT, f"Close {below:.1f}% below the pivot {pivot:.2f}.")
    return Transition(State.BASING, f"Close {below:.1f}% below the pivot {pivot:.2f}.")

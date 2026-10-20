"""A setup in plain English (Phase 8): a few sentences anyone can follow, built only from the
setup's own facts and its verdict (no AI, no invented numbers)."""

from dataclasses import dataclass

from app.scoring.lifecycle import State
from app.scoring.verdict import FUNDAMENTALS_WORDS, Verdict


@dataclass(frozen=True)
class PlainFacts:
    state: str
    kind: str
    rs_rating: int | None
    group_name: str | None
    group_rank: int | None
    groups_ranked: int | None
    fundamentals_grade: str | None
    pattern_label: str | None
    pivot: float | None
    readiness_pct: float | None  # % below the pivot (negative: above)
    volume_ratio: float | None  # today's volume vs the 50-day average
    sessions_since_breakout: int | None = None  # 0 on the breakout day


def _strength(f: PlainFacts) -> str:
    rs = f.rs_rating
    if rs is None:
        first = "There's no strength rating for it yet"
    elif rs >= 50:
        first = f"It has done better than {rs}% of all stocks over the past year"
    else:
        first = f"It has lagged most stocks over the past year (RS Rating {rs})"
    if f.group_name and f.group_rank and f.groups_ranked:
        return (
            f"{first}, and its industry ({f.group_name}) ranks {f.group_rank} of {f.groups_ranked}."
        )
    return f"{first}."


def _business(f: PlainFacts) -> str:
    g = f.fundamentals_grade
    if g is None:
        return "There are no SEC financial statements to grade the business."
    return f"The company shows {FUNDAMENTALS_WORDS.get(g, g)} (Fundamentals Grade {g})."


def _chart(f: PlainFacts) -> str:
    state, pivot, r = State(f.state), f.pivot, f.readiness_pct
    label = f.pattern_label or "base"
    pattern = label[:1].lower() + label[1:]  # "volatility contraction (VCP)"
    if f.kind == "watch" or pivot is None:
        return "It's in an uptrend but hasn't formed a buying pattern (a base) yet."
    if state in (State.FAILED, State.INVALIDATED):
        return "Its last buying pattern didn't work out."
    if state == State.EXTENDED:
        far = "" if r is None else f" {-r:.1f}%"
        return f"It has already run{far} past its buy point of {pivot:.2f}."
    if state == State.BREAKOUT:
        since = f.sessions_since_breakout
        if since is None or since > 0:
            # Today's volume isn't the breakout day's: say when it broke out instead.
            known = since is not None and since < 250  # not one older than the loaded bars
            when = f" {since} session{'s' if since != 1 else ''} ago" if known else ""
            return f"It broke out of a {pattern} above its buy point of {pivot:.2f}{when}."
        heavy = f" on {f.volume_ratio:.1f}× its usual volume" if f.volume_ratio is not None else ""
        return f"It broke out of a {pattern} above its buy point of {pivot:.2f} today{heavy}."
    if r is not None and r < 0:
        return (
            f"It's just above its buy point of {pivot:.2f} in a {pattern}, but without the heavy "
            "buying that confirms a breakout."
        )
    below = "" if r is None else f", {r:.1f}% below its buy point of {pivot:.2f}"
    return f"It's resting in a {pattern}{below}."


def plain_summary(facts: PlainFacts, verdict: Verdict) -> str:
    return " ".join(
        [
            _strength(facts),
            _business(facts),
            _chart(facts),
            f"The app says: {verdict.headline}. {verdict.reason}",
        ]
    )


__all__ = ["PlainFacts", "plain_summary"]

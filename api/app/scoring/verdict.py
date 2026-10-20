"""What to do about a setup today, in plain words (Phase 8): buy, get ready (an order for the
next session), not yet, or don't buy, and every reason with its numbers. Pure.

Checks (each threshold is a DECISIONS setting; the stop limit is `max_stop_loss_pct`):
- market: the regime allows new buys (a confirmed uptrend; optionally also an uptrend under
  pressure, at `decision_under_pressure_size_pct` of the size);
- quality: the Setup Score *before* the market adjustment grades at least
  `decision_min_setup_grade` (the market has its own check, so a correction isn't counted
  twice);
- trend: all 8 Trend Template checks pass;
- strength: RS Rating at least `decision_min_rs_rating`;
- fundamentals: Fundamentals Grade at least `decision_min_fundamentals_grade` (none: fails);
- stop: the base's natural stop is within the maximum loss of the entry (and, buying after the
  breakout, of today's close);
- earnings: no results due within `decision_earnings_block_sessions` sessions.

Then the stage decides:
- BUY: a confirmed breakout at most `decision_buy_window_sessions` after the breakout day,
  closing inside the buy zone (pivot to `buy_zone_max_pct_above_pivot` above it);
- GET_READY: near the pivot (the NEAR_PIVOT stage: within `near_pivot_pct` below it, or above
  it without a confirmed breakout, inside the buy zone): an order for the next session;
- WAIT ("not yet"): everything passes but there's nothing to buy yet: basing further below the
  pivot, a trend leader without a base, or a breakout already well under way;
- AVOID ("don't buy"): any check fails, or the setup is extended, failed or invalidated.
"""

import math
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any

from app.scoring.lifecycle import State
from app.scoring.setup_score import letter
from app.settings.schema import AppSettings

GRADE_ORDER = {"A+": 4, "A": 3, "B": 2, "C": 1}
FUNDAMENTALS_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
FUNDAMENTALS_WORDS = {
    "A": "very strong profit and sales growth",
    "B": "strong profit and sales growth",
    "C": "average profit and sales growth",
    "D": "weak profit and sales growth",
    "E": "poor profit and sales growth",
}
REGIME_WORDS = {
    "confirmed_uptrend": "rising (confirmed uptrend)",
    "uptrend_under_pressure": "rising but under pressure",
    "correction": "falling (correction)",
}


class Action(StrEnum):
    BUY = "buy"
    GET_READY = "get_ready"
    WAIT = "wait"
    AVOID = "avoid"


HEADLINES = {
    Action.BUY: "Buy signal",
    Action.GET_READY: "Get ready",
    Action.WAIT: "Not yet",
    Action.AVOID: "Don't buy",
}


@dataclass(frozen=True)
class Check:
    key: str
    label: str  # the rule, in words
    passed: bool
    detail: str  # what the stock actually has, with numbers
    short: str  # the failure in a few words, for a sentence ("the market is falling")


@dataclass(frozen=True)
class VerdictInputs:
    state: str  # the lifecycle stage
    kind: str  # watch | base | episodic_pivot
    active: bool
    close: float
    pivot: float | None
    readiness_pct: float | None  # % below the pivot (negative: above)
    sessions_since_breakout: int | None
    raw_score: float
    penalties: float
    market_state: str | None
    market_label: str | None
    tt_passed: int | None
    rs_rating: int | None
    fundamentals_grade: str | None
    sessions_to_earnings: int | None
    next_earnings: str | None  # ISO date
    trade_plan: dict[str, Any] | None


@dataclass(frozen=True)
class Verdict:
    action: Action
    headline: str
    reason: str  # one sentence: why this action
    checks: list[Check]
    order: dict[str, Any] | None = None
    size_pct: float = 100  # % of the normal position size (reduced under pressure)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": str(self.action),
            "headline": self.headline,
            "reason": self.reason,
            "checks": [asdict(c) for c in self.checks],
            "order": self.order,
            "size_pct": self.size_pct,
            "notes": self.notes,
            "passed": sum(c.passed for c in self.checks),
            "total": len(self.checks),
        }


def _market(i: VerdictInputs, s: AppSettings) -> Check:
    label = "New buys only when the market is rising"
    state = i.market_state
    words = REGIME_WORDS.get(state or "", "unknown")
    if state == "confirmed_uptrend":
        return Check("market", label, True, f"The market is {words}.", "")
    if state == "uptrend_under_pressure" and s.decision_regimes == "confirmed_or_under_pressure":
        return Check(
            "market",
            label,
            True,
            f"The market is {words}: buy at {s.decision_under_pressure_size_pct:g}% of the "
            "normal size.",
            "",
        )
    detail = (
        f"The market is {words}. Most breakouts fail when the market isn't in a confirmed uptrend."
    )
    if state is None:
        detail = "The market regime isn't known yet."
    return Check("market", label, False, detail, f"the market is {words}")


def _quality(i: VerdictInputs, s: AppSettings) -> Check:
    need = s.decision_min_setup_grade
    cutoffs = s.setup_grade_cutoffs
    need_score = {"A+": cutoffs.a_plus, "A": cutoffs.a, "B": cutoffs.b, "C": cutoffs.c}[need]
    quality = max(0.0, i.raw_score - i.penalties)
    grade = letter(round(quality, 2), s)
    passed = GRADE_ORDER.get(grade or "", 0) >= GRADE_ORDER[need]
    shown = f"{quality:.0f}/100 ({grade if grade else 'below C'})"
    label = f"A setup graded {need} or better (before the market adjustment)"
    detail = f"Setup quality {shown}; {need} needs {need_score:g}."
    return Check("quality", label, passed, detail, f"the setup scores only {shown}")


def _trend(i: VerdictInputs) -> Check:
    label = "A proper uptrend (all 8 Trend Template checks)"
    n = i.tt_passed
    if n == 8:
        return Check("trend", label, True, "8 of 8 trend checks pass.", "")
    shown = "unknown" if n is None else f"{n} of 8"
    return Check(
        "trend",
        label,
        False,
        f"Only {shown} trend checks pass: not a proper uptrend.",
        f"it isn't in a proper uptrend ({shown} checks)",
    )


def _strength(i: VerdictInputs, s: AppSettings) -> Check:
    need = s.decision_min_rs_rating
    label = f"Stronger than most stocks (RS Rating {need}+)"
    rs = i.rs_rating
    if rs is None:
        return Check("strength", label, False, "No RS Rating yet.", "it has no RS Rating yet")
    detail = f"RS Rating {rs}: it has done better than {rs}% of all stocks over the past year."
    return Check("strength", label, rs >= need, detail, f"its RS Rating is only {rs}")


def _fundamentals(i: VerdictInputs, s: AppSettings) -> Check:
    need = s.decision_min_fundamentals_grade
    label = f"A sound business (Fundamentals Grade {need} or better)"
    g = i.fundamentals_grade
    if g is None:
        return Check(
            "fundamentals",
            label,
            False,
            "No Fundamentals Grade: no financial statements from the SEC.",
            "its finances are unknown",
        )
    words = FUNDAMENTALS_WORDS.get(g, g)
    passed = FUNDAMENTALS_ORDER.get(g, 0) >= FUNDAMENTALS_ORDER[need]
    return Check(
        "fundamentals",
        label,
        passed,
        f"Grade {g}: {words}.",
        f"the company has {words} (grade {g})",
    )


def _stop(i: VerdictInputs, s: AppSettings, buying_at: float | None) -> Check:
    limit = s.max_stop_loss_pct
    label = f"A stop within {limit:g}% of the buy price"
    plan = i.trade_plan
    if plan is None or "entry" not in plan or "stop" not in plan or "buy_zone" not in plan:
        return Check("stop", label, False, "No trade plan yet.", "it has no trade plan")
    entry = float(plan["entry"])
    if plan.get("risk_too_wide"):
        natural = float(plan.get("logical_stop", plan["stop"]))
        width = (entry - natural) / entry * 100
        return Check(
            "stop",
            label,
            False,
            f"The natural stop under the base ({natural:.2f}) is {width:.1f}% below the entry "
            f"{entry:.2f}: too much risk. Wait for a tighter setup.",
            f"the stop would be {width:.1f}% away (max {limit:g}%)",
        )
    stop = float(plan["stop"])
    price = entry if buying_at is None else max(entry, buying_at)
    width = (price - stop) / price * 100
    if width > limit + 1e-9:
        return Check(
            "stop",
            label,
            False,
            f"Buying at {price:.2f}, the stop {stop:.2f} is {width:.1f}% away: too far.",
            f"the stop would be {width:.1f}% away (max {limit:g}%)",
        )
    return Check("stop", label, True, f"Stop {stop:.2f}, {width:.1f}% below {price:.2f}.", "")


def _earnings(i: VerdictInputs, s: AppSettings) -> Check:
    block = s.decision_earnings_block_sessions
    label = f"No results due within {block} sessions" if block else "Earnings (ignored)"
    n, when = i.sessions_to_earnings, i.next_earnings
    if block == 0:
        return Check("earnings", label, True, "Earnings dates are ignored by your settings.", "")
    if n is None:
        return Check("earnings", label, True, "No upcoming results date known.", "")
    on = f" (about {when})" if when else ""
    if n <= block:
        return Check(
            "earnings",
            label,
            False,
            f"Results due in {n} sessions{on}: a gap after results can jump straight past the "
            "stop.",
            f"results are due in {n} sessions",
        )
    return Check("earnings", label, True, f"Next results in {n} sessions{on}.", "")


def shares_for(price: float, stop: float, s: AppSettings, size_pct: float = 100) -> int:
    """Shares risking `risk_per_trade_pct` of the account buying at `price` with `stop`,
    capped at `max_position_pct`, then scaled to `size_pct`."""
    risk = round(price - stop, 2)
    if risk <= 0 or price <= 0:
        return 0
    by_risk = math.floor(s.account_size * s.risk_per_trade_pct / 100 / risk)
    by_position = math.floor(s.account_size * s.max_position_pct / 100 / price)
    return max(0, math.floor(min(by_risk, by_position) * size_pct / 100))


def _order(i: VerdictInputs, action: Action, s: AppSettings, size_pct: float) -> dict[str, Any]:
    plan = i.trade_plan or {}
    entry, stop = float(plan["entry"]), float(plan["stop"])
    zone_top = float(plan["buy_zone"][1])
    if action == Action.BUY:
        price = max(entry, i.close)
        shares = shares_for(price, stop, s, size_pct)
        return {
            "type": "buy_now",
            "price": round(price, 2),
            "limit": zone_top,
            "stop": stop,
            "shares": shares,
            "text": f"Buy {shares} shares at up to {zone_top:.2f} (it closed at {i.close:.2f}), "
            f"with a stop-loss order at {stop:.2f}. Don't pay more than {zone_top:.2f}.",
        }
    shares = shares_for(entry, stop, s, size_pct)
    if i.close >= entry:
        return {
            "type": "on_volume",
            "price": entry,
            "limit": zone_top,
            "stop": stop,
            "shares": shares,
            "text": f"It's already above the pivot without heavy buying. Buy {shares} shares only "
            f"on a day it trades on heavy volume (watch the Live page), at up to "
            f"{zone_top:.2f}, with a stop-loss at {stop:.2f}.",
        }
    return {
        "type": "buy_stop",
        "price": entry,
        "limit": zone_top,
        "stop": stop,
        "shares": shares,
        "text": f"Place a buy-stop order for {shares} shares at {entry:.2f} (limit "
        f"{zone_top:.2f}), then a stop-loss at {stop:.2f}. If the price never reaches "
        f"{entry:.2f}, nothing is bought.",
    }


def decide(i: VerdictInputs, s: AppSettings) -> Verdict:
    state = State(i.state)
    zone_pct = s.buy_zone_max_pct_above_pivot
    buying_now = state == State.BREAKOUT
    checks = [
        _market(i, s),
        _quality(i, s),
        _trend(i),
        _strength(i, s),
        _fundamentals(i, s),
        _stop(i, s, i.close if buying_now else None),
        _earnings(i, s),
    ]
    # A passing check has nothing to say in a failure sentence.
    checks = [c if not c.passed else replace(c, short="") for c in checks]
    size_pct = 100.0
    if i.market_state == "uptrend_under_pressure" and checks[0].passed:
        size_pct = s.decision_under_pressure_size_pct
    failed = [c for c in checks if not c.passed]
    readiness = i.readiness_pct

    def verdict(action: Action, reason: str, order: dict[str, Any] | None = None) -> Verdict:
        return Verdict(action, HEADLINES[action], reason, checks, order, size_pct)

    # The stage first: some stages are never a buy, whatever the rules say.
    if not i.active or state in (State.FAILED, State.INVALIDATED):
        return verdict(Action.AVOID, "The setup has ended: there's nothing to buy.")
    if state == State.EXTENDED:
        above = "" if readiness is None else f" ({-readiness:.1f}% above the pivot)"
        return verdict(
            Action.AVOID,
            f"Extended{above}: past the {zone_pct:g}% buy zone. Too late, don't chase it.",
        )
    if failed:
        reasons = "; ".join(c.short for c in failed)
        n = len(failed)
        return verdict(
            Action.AVOID,
            f"It fails {n} of the app's {len(checks)} rules: {reasons}.",
        )
    if i.kind == "watch" or i.pivot is None:
        return verdict(Action.WAIT, "A strong stock without a base yet: there's no buy point.")
    if state == State.BREAKOUT:
        since = i.sessions_since_breakout
        zone_top = i.pivot * (1 + zone_pct / 100)
        if since is not None and since <= s.decision_buy_window_sessions and i.close <= zone_top:
            when = "today" if since == 0 else f"{since} session{'s' if since > 1 else ''} ago"
            return verdict(
                Action.BUY,
                f"It broke out {when} and passes every rule.",
                _order(i, Action.BUY, s, size_pct),
            )
        return verdict(
            Action.WAIT,
            "The breakout is already under way: wait for a pullback buy point or a new base.",
        )
    if state == State.NEAR_PIVOT:
        where = (
            f"{readiness:.1f}% below its pivot"
            if readiness is not None and readiness >= 0
            else "just above its pivot without a confirmed breakout"
        )
        return verdict(
            Action.GET_READY,
            f"It's {where} and passes every rule: set up the order.",
            _order(i, Action.GET_READY, s, size_pct),
        )
    below = "" if readiness is None else f" {readiness:.1f}% below its pivot"
    return verdict(
        Action.WAIT,
        f"Still basing{below}. It becomes 'Get ready' within {s.near_pivot_pct:g}% of the pivot.",
    )


__all__ = ["HEADLINES", "Action", "Check", "Verdict", "VerdictInputs", "decide", "shares_for"]

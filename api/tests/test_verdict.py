"""The buy / get ready / not yet / don't buy verdict, worked by hand with the default settings
($100,000 account, 1% risk, 25% max position, 8% max stop, buy zone 5%, near pivot 3%,
setup grade B = 70, fundamentals C+, RS 80+, no buys within 10 sessions of results)."""

from dataclasses import replace
from typing import Any

from app.scoring.verdict import Action, VerdictInputs, decide, shares_for
from app.settings.schema import DEFAULTS


def plan(entry: float = 20.0, stop: float = 18.8, **extra: Any) -> dict[str, Any]:
    pivot = round(entry - 0.10, 2)
    return {
        "entry": entry,
        "stop": stop,
        "logical_stop": stop,
        "risk_too_wide": False,
        "buy_zone": [pivot, round(pivot * 1.05, 2)],
        **extra,
    }


GOOD = VerdictInputs(
    state="near_pivot",
    kind="base",
    active=True,
    close=19.5,
    pivot=19.9,
    readiness_pct=2.05,
    sessions_since_breakout=None,
    raw_score=82.0,
    penalties=0.0,
    market_state="confirmed_uptrend",
    market_label="Confirmed uptrend",
    tt_passed=8,
    rs_rating=92,
    fundamentals_grade="A",
    sessions_to_earnings=30,
    next_earnings="2026-12-01",
    trade_plan=plan(),
)


def test_a_clean_setup_near_its_pivot_gets_an_order_for_tomorrow() -> None:
    v = decide(GOOD, DEFAULTS)
    assert v.action == Action.GET_READY
    assert v.headline == "Get ready"
    assert all(c.passed for c in v.checks)
    assert len(v.checks) == 7
    # $1,000 risk ÷ (20.00 - 18.80) = 833.3 → 833 shares; 25% of $100,000 ÷ 20 = 1,250.
    assert v.order is not None
    assert (v.order["type"], v.order["price"], v.order["shares"]) == ("buy_stop", 20.0, 833)
    assert v.order["limit"] == 20.89  # pivot 19.90 × 1.05 = 20.895 → 20.89
    assert "Place a buy-stop order for 833 shares at 20.00" in v.order["text"]


def test_a_fresh_confirmed_breakout_inside_the_buy_zone_is_a_buy() -> None:
    day = replace(GOOD, state="breakout", close=20.3, readiness_pct=-2.0, sessions_since_breakout=0)
    v = decide(day, DEFAULTS)
    assert v.action == Action.BUY
    assert v.headline == "Buy signal"
    assert v.reason == "It broke out today and passes every rule."
    # Buying at the 20.30 close: $1,000 ÷ (20.30 - 18.80) = 666.7 → 666 shares (the stop is
    # 7.4% below 20.30: within 8%).
    assert v.order is not None
    assert (v.order["type"], v.order["price"], v.order["shares"]) == ("buy_now", 20.3, 666)


def test_an_older_breakout_is_no_longer_a_buy() -> None:
    day = replace(GOOD, state="breakout", close=20.3, sessions_since_breakout=4)
    v = decide(day, DEFAULTS)
    assert v.action == Action.WAIT
    assert "already under way" in v.reason


def test_buying_far_above_the_entry_puts_the_stop_too_far_away() -> None:
    # 20.85 is inside the buy zone, but the 18.80 stop is 9.8% below it.
    day = replace(GOOD, state="breakout", close=20.85, sessions_since_breakout=1)
    v = decide(day, DEFAULTS)
    assert v.action == Action.AVOID
    [failed] = v.failed
    assert failed.key == "stop"
    assert failed.detail == "Buying at 20.85, the stop 18.80 is 9.8% away: too far."


def test_prch_breakout_fails_four_rules() -> None:
    # A confirmed breakout in a correction: setup quality 63.7 (C), fundamentals E, natural
    # stop 16.05 is 11.2% below the 18.08 entry, results in 11 sessions (allowed: > 10).
    prch = replace(
        GOOD,
        state="breakout",
        close=18.18,
        pivot=17.98,
        readiness_pct=-1.11,
        sessions_since_breakout=0,
        raw_score=63.7,
        market_state="correction",
        market_label="Correction",
        rs_rating=97,
        fundamentals_grade="E",
        sessions_to_earnings=11,
        next_earnings="2026-11-04",
        trade_plan=plan(entry=18.08, stop=16.64, logical_stop=16.05, risk_too_wide=True),
    )
    v = decide(prch, DEFAULTS)
    assert v.action == Action.AVOID
    assert v.headline == "Don't buy"
    assert [c.key for c in v.failed] == ["market", "quality", "fundamentals", "stop"]
    assert v.reason == (
        "It fails 4 of the app's 7 rules: the market is falling (correction); the setup scores "
        "only 64/100 (C); the company has poor profit and sales growth (grade E); the stop "
        "would be 11.2% away (max 8%)."
    )
    assert v.order is None
    earnings = next(c for c in v.checks if c.key == "earnings")
    assert earnings.passed
    assert earnings.detail == "Next results in 11 sessions (about 2026-11-04)."


def test_quality_is_judged_before_the_market_adjustment() -> None:
    # In an uptrend under pressure the score is × 0.8 elsewhere; the quality rule uses 82.
    pressured = replace(GOOD, market_state="uptrend_under_pressure")
    v = decide(pressured, DEFAULTS)
    assert v.action == Action.AVOID  # the default allows buys only in a confirmed uptrend
    assert [c.key for c in v.failed] == ["market"]
    assert next(c for c in v.checks if c.key == "quality").detail == (
        "Setup quality 82/100 (A); B needs 70."
    )


def test_an_uptrend_under_pressure_can_allow_buys_at_half_size() -> None:
    allowing = DEFAULTS.model_copy(update={"decision_regimes": "confirmed_or_under_pressure"})
    v = decide(replace(GOOD, market_state="uptrend_under_pressure"), allowing)
    assert v.action == Action.GET_READY
    assert v.size_pct == 50
    assert v.order is not None
    assert v.order["shares"] == 416  # half of 833


def test_results_due_soon_block_new_buys_unless_switched_off() -> None:
    soon = replace(GOOD, sessions_to_earnings=6, next_earnings="2026-10-28")
    v = decide(soon, DEFAULTS)
    assert v.action == Action.AVOID
    assert v.failed[0].short == "results are due in 6 sessions"
    ignoring = DEFAULTS.model_copy(update={"decision_earnings_block_sessions": 0})
    assert decide(soon, ignoring).action == Action.GET_READY


def test_above_the_pivot_without_heavy_volume_waits_for_volume() -> None:
    above = replace(GOOD, close=20.2, readiness_pct=-1.5)
    v = decide(above, DEFAULTS)
    assert v.action == Action.GET_READY
    assert v.reason == (
        "It's just above its pivot without a confirmed breakout and passes every rule: set up "
        "the order."
    )
    assert v.order is not None
    assert v.order["type"] == "on_volume"


def test_basing_far_below_the_pivot_or_without_a_base_is_not_yet() -> None:
    basing = replace(GOOD, state="basing", readiness_pct=6.4)
    v = decide(basing, DEFAULTS)
    assert v.action == Action.WAIT
    assert v.headline == "Not yet"
    assert (
        v.reason
        == "Still basing 6.4% below its pivot. It becomes 'Get ready' within 3% of the pivot."
    )
    leader = replace(GOOD, state="watch", kind="watch", pivot=None, trade_plan=plan())
    assert decide(leader, DEFAULTS).action == Action.WAIT


def test_extended_failed_and_ended_setups_are_never_a_buy() -> None:
    extended = replace(GOOD, state="extended", readiness_pct=-7.0)
    v = decide(extended, DEFAULTS)
    assert v.action == Action.AVOID
    assert v.reason.startswith("Extended (7.0% above the pivot): past the 5% buy zone")
    assert decide(replace(GOOD, state="failed"), DEFAULTS).action == Action.AVOID
    assert decide(replace(GOOD, active=False), DEFAULTS).action == Action.AVOID


def test_missing_data_fails_safe() -> None:
    unknown = replace(GOOD, fundamentals_grade=None, rs_rating=None, market_state=None)
    v = decide(unknown, DEFAULTS)
    assert v.action == Action.AVOID
    assert {c.key for c in v.failed} == {"market", "strength", "fundamentals"}


def test_shares_never_exceed_the_position_cap() -> None:
    # A 0.30 risk would allow 3,333 shares; 25% of $100,000 ÷ 50 caps it at 500.
    assert shares_for(50.0, 49.7, DEFAULTS) == 500
    assert shares_for(50.0, 50.0, DEFAULTS) == 0


def test_the_verdict_serialises_for_storage() -> None:
    d = decide(GOOD, DEFAULTS).as_dict()
    assert (d["action"], d["passed"], d["total"]) == ("get_ready", 7, 7)
    assert d["checks"][0] == {
        "key": "market",
        "label": "New buys only when the market is rising",
        "passed": True,
        "detail": "The market is rising (confirmed uptrend).",
        "short": "",
    }

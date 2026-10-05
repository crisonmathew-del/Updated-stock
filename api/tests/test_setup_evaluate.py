"""One stock, one session: setups open, convert, switch, break out, fail and get superseded,
with the transitions and signals each produces (default settings throughout)."""

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from app.patterns.bars import Bars
from app.patterns.types import PatternMatch, PatternType, Point, ScoreComponent, Status
from app.scanner.evaluate import (
    Detected,
    MarketDay,
    SetupRecord,
    StockDay,
    StockResult,
    Technicals,
    Tracked,
    TransitionDraft,
    evaluate_stock,
)
from app.scoring.lifecycle import State
from app.settings.schema import DEFAULTS
from tests.pattern_fixtures import plain_bars, sessions
from tests.test_triggers import pullback_bars, undercut_bars

N = 40
DAYS = sessions(N)
TODAY = DAYS[-1]
LEADER = Technicals(
    tt_passed=8, tt_pass=True, stage=2, rs_rating=90, rs_line_high_52w=True, up_down_volume=1.3
)
LAGGARD = Technicals(tt_passed=4, tt_pass=False, stage=3, rs_rating=40)


def bars(closes: list[float], **days: tuple[float, float, float]) -> Bars:
    """±0.5 ranges and 1000 shares a day (the 50-day average), 21-day EMA 96, 50-day SMA 94.
    `days`: "i" = (high, low, volume) overrides, e.g. d39=(102.5, 99.0, 1500.0)."""
    high = [c + 0.5 for c in closes]
    low = [c - 0.5 for c in closes]
    volume = [1000.0] * len(closes)
    for key, (h, lo, v) in days.items():
        i = int(key[1:])
        high[i], low[i], volume[i] = h, lo, v
    n = len(closes)
    built = plain_bars(high, low, closes, volume=volume, avg_volume=1000.0)
    return replace(built, ema21=np.full(n, 96.0), sma50=np.full(n, 94.0))


def base(
    *,
    type_: PatternType = PatternType.VCP,
    status: Status = Status.FORMING,
    pivot: float = 100.0,
    low: float = 90.0,
    start: int = 5,
    quality: float = 80.0,
    breakout: int | None = None,
    lows: tuple[tuple[int, float], ...] = ((20, 92.0), (30, 94.0)),
    days: list[date] = DAYS,
) -> PatternMatch:
    return PatternMatch(
        type=type_,
        timeframe="daily",
        start=days[start],
        end=days[-1],
        pivot=pivot,
        status=status,
        duration_weeks=7.0,
        components=[ScoreComponent("all", "Everything", quality, 100, "")],
        base_low=low,
        base_number=1,
        swings=[Point(days[i], price, "low") for i, price in lows],
        details={"breakout_date": None if breakout is None else days[breakout].isoformat()},
    )


def stock(b: Bars, *detected: Detected, tech: Technicals = LEADER, **kw: object) -> StockDay:
    return StockDay(ticker_id=1, bars=b, tech=tech, detected=tuple(detected), **kw)  # type: ignore[arg-type]


def setup(
    state: State, *, pattern: PatternMatch | None = None, pid: int = 11, **kw: object
) -> SetupRecord:
    fields: dict[str, object] = {
        "ticker_id": 1,
        "kind": "base" if pattern is not None else "watch",
        "state": state,
        "state_since": DAYS[-2],
        "first_seen": DAYS[-10],
        "as_of": DAYS[-2],
        "close": 98.0,
        "id": 7,
        "best_grade": "A",
    }
    if pattern is not None:
        fields |= {
            "pattern": Tracked.from_match(pid, pattern),
            "pivot": pattern.pivot,
            "base_low": pattern.base_low,
        }
    fields |= kw
    return SetupRecord(**fields)  # type: ignore[arg-type]


def run(s: StockDay, active: SetupRecord | None = None, as_of: date = TODAY) -> StockResult:
    return evaluate_stock(s, active, MarketDay(), DEFAULTS, as_of)


def types(result: StockResult) -> set[str]:
    return {d.type for w in result.writes for d in w.signals} | {d.type for d in result.signals}


def test_a_new_base_near_its_pivot() -> None:
    result = run(stock(bars([95.0] * 39 + [98.0]), Detected(11, base())))
    [write] = result.writes
    rec = write.record
    assert (rec.kind, rec.state, rec.first_seen, rec.active) == (
        "base",
        State.NEAR_PIVOT,
        TODAY,
        True,
    )
    assert (rec.pivot, rec.base_low, rec.pattern and rec.pattern.id) == (100.0, 90.0, 11)
    assert write.transitions == [
        TransitionDraft(
            TODAY,
            None,
            "near_pivot",
            "New volatility contraction (VCP) setup (pivot 100.00). Close 2.0% below the pivot "
            "100.00.",
        )
    ]
    # Entry 100.00 + 0.10; logical stop just under the last swing low 94 (× 0.999 = 93.906).
    assert rec.trade_plan is not None
    assert (rec.trade_plan["entry"], rec.trade_plan["stop"]) == (100.10, 93.90)
    assert rec.trade_plan["stop_basis"] == "logical"
    # Trend 20 + RS 20 × (0.7 × 40/49 + 0.3) + pattern 16 + accumulation 5, out of the 70
    # points with data (no fundamentals grade, no group): 58.43 / 70 = 83.47 → A.
    assert rec.score is not None
    assert (rec.score.raw, rec.score.grade, rec.score.coverage_pct) == (83.47, "A", 70.0)
    assert rec.readiness == 2.04
    summaries = {d.type: d.summary for d in write.signals}
    assert summaries == {
        "new_top_setup": "New A setup: volatility contraction (VCP) scoring 83/100 (pivot "
        "100.00, close 2.0% below the pivot).",
        "near_pivot": "Near pivot: New volatility contraction (VCP) setup (pivot 100.00). Close "
        "2.0% below the pivot 100.00.",
    }


def test_a_confirmed_breakout_freezes_the_plan() -> None:
    active = setup(State.NEAR_PIVOT, pattern=base())
    b = bars([95.0] * 38 + [98.0, 102.0], d39=(102.5, 99.0, 1500.0))
    result = run(stock(b, Detected(11, base(status=Status.BROKEN_OUT, breakout=39))), active)
    [write] = result.writes
    rec = write.record
    assert (rec.id, rec.state, rec.breakout_date, rec.state_since) == (
        7,
        State.BREAKOUT,
        TODAY,
        TODAY,
    )
    assert rec.trade_plan is not None
    assert rec.trade_plan["stop"] == 93.90
    assert [d.summary for d in write.signals] == [
        "Breakout confirmed: Closed 2.0% above the pivot 100.00 on 150% of average volume, 86% "
        "up the day's range."
    ]  # best grade was already A: no new-setup signal

    # The next session holds in the buy zone; the plan stays frozen even if the low moves.
    later = bars([95.0] * 37 + [98.0, 102.0, 103.0], d38=(102.5, 99.0, 1500.0))
    moved = base(status=Status.BROKEN_OUT, breakout=38, lows=((30, 97.0),))
    rec.breakout_date = DAYS[38]
    nxt = run(stock(later, Detected(11, moved)), rec)
    assert nxt.writes[0].record.trade_plan == rec.trade_plan
    assert nxt.writes[0].transitions == []
    assert types(nxt) == set()


def test_a_weak_cross_is_reported_once_as_rejected() -> None:
    active = setup(State.NEAR_PIVOT, pattern=base())
    b = bars([95.0] * 38 + [98.0, 101.0], d39=(101.0, 99.0, 1000.0))  # closed at the high
    result = run(stock(b, Detected(11, base())), active)
    [write] = result.writes
    assert write.record.state == State.NEAR_PIVOT
    assert [d.summary for d in write.signals] == [
        "Breakout rejected: Closed above the pivot 100.00, but volume was 100% of average "
        "(needs 140%)."
    ]
    # Still above the pivot the next day: not a fresh cross, so no second signal.
    again = run(stock(bars([95.0] * 37 + [98.0, 101.0, 101.2]), Detected(11, base())), active)
    assert types(again) == set()


def test_a_base_found_after_its_breakout_is_replayed_from_that_session() -> None:
    closes = [95.0] * 36 + [97.0, 101.5, 103.0, 104.0]
    b = bars(closes, d37=(101.6, 99.0, 2000.0))
    result = run(stock(b, Detected(11, base(status=Status.BROKEN_OUT, breakout=37))))
    [write] = result.writes
    rec = write.record
    assert (rec.state, rec.breakout_date, rec.first_seen) == (State.BREAKOUT, DAYS[37], TODAY)
    assert write.transitions == [
        TransitionDraft(
            DAYS[37],
            None,
            "breakout",
            "New volatility contraction (VCP) setup (pivot 100.00). Closed 1.5% above the pivot "
            "100.00 on 200% of average volume, 96% up the day's range.",
        )
    ]
    assert write.today is not None
    assert write.today.reason == "Holding +4.0% from the pivot 100.00."
    assert "breakout" not in types(result)  # it broke out two sessions ago, not today


def test_a_failed_breakout_closes_and_the_leader_goes_back_on_watch() -> None:
    pattern = base(status=Status.BROKEN_OUT, breakout=38)
    active = setup(
        State.BREAKOUT, pattern=pattern, breakout_date=DAYS[38], trade_plan={"stop": 93.9}
    )
    b = bars([95.0] * 38 + [101.0, 99.5], d39=(100.5, 99.0, 1200.0))
    result = run(stock(b, Detected(11, replace(pattern, status=Status.FORMING))), active)
    closed, watch = result.writes
    assert (closed.record.state, closed.record.active, closed.record.closed_on) == (
        State.FAILED,
        False,
        TODAY,
    )
    assert closed.record.closed_reason == (
        "Closed back below the pivot 100.00 (99.50) 1 session(s) after the breakout."
    )
    assert [d.type for d in closed.signals] == ["failed"]
    # The failed base is spent: the new setup is a watch, not the same base again.
    assert (watch.record.kind, watch.record.state, watch.record.id) == ("watch", State.WATCH, None)
    assert watch.transitions[0].reason == (
        "Trend leader without a base. Trend Template 8/8, RS Rating 90."
    )


def test_a_watch_converts_in_place_when_a_base_appears() -> None:
    result = run(stock(bars([95.0] * 40), Detected(11, base())), setup(State.WATCH))
    [write] = result.writes
    assert (write.record.id, write.record.kind, write.record.state) == (7, "base", State.BASING)
    assert write.transitions == [
        TransitionDraft(
            TODAY,
            "watch",
            "basing",
            "Tracking a volatility contraction (VCP) (pivot 100.00). Close 5.0% below the pivot "
            "100.00.",
        )
    ]


def test_a_watch_that_stops_leading_is_closed_quietly() -> None:
    result = run(stock(bars([95.0] * 40), tech=LAGGARD), setup(State.WATCH))
    [write] = result.writes
    assert (write.record.state, write.record.active) == (State.INVALIDATED, False)
    assert write.record.closed_reason == (
        "No longer a trend leader. Trend Template 4/8, RS Rating 40."
    )
    assert types(result) == set()  # watches aren't alerts


def test_a_base_that_stops_matching_switches_to_another_current_base() -> None:
    active = setup(State.BASING, pattern=base())
    flat = base(type_=PatternType.FLAT_BASE, pivot=99.0, low=92.0, start=15, lows=((25, 93.0),))
    result = run(stock(bars([95.0] * 40), Detected(12, flat)), active)
    [write] = result.writes
    rec = write.record
    assert (rec.id, rec.pivot, rec.pattern and rec.pattern.id) == (7, 99.0, 12)
    assert write.transitions == [
        TransitionDraft(
            TODAY,
            "basing",
            "basing",
            "Switched to a flat base (pivot 99.00). Close 4.0% below the pivot 99.00.",
        )
    ]
    # Without another base it is invalidated.
    alone = run(stock(bars([95.0] * 40), tech=LAGGARD), setup(State.BASING, pattern=base()))
    assert alone.writes[0].record.closed_reason == "The base no longer meets its rules."
    assert types(alone) == {"invalidated"}


def test_a_new_base_after_the_breakout_supersedes_the_setup() -> None:
    old = base(pivot=80.0, low=70.0, start=0, lows=((5, 72.0),))
    active = setup(
        State.EXTENDED,
        pattern=old,
        breakout_date=DAYS[10],
        trade_plan={"stop": 73.0},
        first_seen=DAYS[2],
    )
    flat = base(type_=PatternType.FLAT_BASE, pivot=99.0, low=92.0, start=20, lows=((25, 93.0),))
    result = run(stock(bars([95.0] * 39 + [97.0]), Detected(12, flat)), active)
    closed, new = result.writes
    assert (closed.record.active, closed.record.state) == (False, State.EXTENDED)
    assert closed.record.closed_reason == "Superseded by a new flat base (pivot 99.00)."
    assert (new.record.id, new.record.pivot, new.record.state) == (None, 99.0, State.NEAR_PIVOT)


def test_event_signals_without_a_setup() -> None:
    pivot = PatternMatch(
        type=PatternType.POCKET_PIVOT,
        timeframe="daily",
        start=TODAY,
        end=TODAY,
        pivot=95.0,
        status=Status.FORMING,
        duration_weeks=0.2,
        components=[ScoreComponent("all", "Everything", 70.0, 100, "")],
        details={"volume_vs_down_max": 1.6},
    )
    result = run(stock(bars([95.0] * 40), Detected(21, pivot), tech=LAGGARD))
    assert result.writes == []
    assert [(d.type, d.summary) for d in result.signals] == [
        (
            "pocket_pivot",
            "Pocket pivot: volume 1.6× the largest down day of the previous 10 sessions, "
            "quality 70/100.",
        )
    ]


def test_rs_new_high_ahead_only_when_it_turns_on() -> None:
    ahead = replace(LEADER, rs_new_high_ahead=True)
    first = run(stock(bars([95.0] * 40), Detected(11, base()), tech=ahead))
    assert "rs_new_high_ahead" in types(first)
    still = replace(ahead, rs_new_high_ahead_before=True)
    assert "rs_new_high_ahead" not in types(
        run(stock(bars([95.0] * 40), Detected(11, base()), tech=still))
    )


def test_secondary_entries_reach_the_signal_log() -> None:
    # Undercut & rally inside a base: tests.test_triggers draws it (level 95 on session 10).
    ub = undercut_bars()
    udays = sessions(len(ub))
    pattern = base(pivot=101.0, low=90.0, start=0, lows=((10, 95.0),), days=udays)
    result = evaluate_stock(
        stock(ub, Detected(11, pattern)), None, MarketDay(), DEFAULTS, udays[-1]
    )
    assert "undercut_rally" in types(result)

    # A pullback to the 10-day EMA 12 sessions after a breakout (tests.test_triggers again).
    pb = pullback_bars()
    pdays = sessions(len(pb))
    old = base(pivot=101.0, low=90.0, start=0, lows=((15, 99.0),), days=pdays)
    active = setup(
        State.BREAKOUT,
        pattern=old,
        breakout_date=pdays[20],
        trade_plan={"stop": 95.0},
        as_of=pdays[-2],
    )
    tech = replace(LEADER, rs_rating=92)
    result = evaluate_stock(stock(pb, tech=tech), active, MarketDay(), DEFAULTS, pdays[-1])
    assert result.writes[0].record.state == State.BREAKOUT
    assert [d.type for d in result.writes[0].signals] == ["pullback"]


def test_bars_must_end_on_the_session() -> None:
    with pytest.raises(ValueError, match="no bar"):
        run(stock(bars([95.0] * 40)), as_of=date(2030, 1, 2))

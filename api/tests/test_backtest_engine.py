"""Stage 2 by hand: fills, sizing, costs and every exit rule on tiny price books, plus the
metrics (drawdown, CAGR, Sharpe/Sortino, trade statistics) worked out in the comments."""

import math
from datetime import date

import numpy as np
import pytest

from app.backtest.engine import (
    BacktestParams,
    Candidate,
    Exits,
    Portfolio,
    PriceBook,
    Rules,
    Trade,
    simulate,
)
from app.backtest.metrics import breakdown, drawdowns, equity_metrics, trade_metrics
from app.core.calendar import sessions_between

DAYS = sessions_between(date(2025, 3, 3), date(2025, 6, 30))
NAN = math.nan

# No costs, no extra rules: each test switches on what it checks.
PLAIN = Exits(
    sell_unconfirmed=False,
    trailing="none",
    time_stop_sessions=0,
    partial_fraction_pct=0,
    breakeven_r=100,
    breakeven_gain_pct=1000,
)


def params(exits: Exits = PLAIN, *, slippage: float = 0, **portfolio: float) -> BacktestParams:
    return BacktestParams(
        start=DAYS[0],
        end=DAYS[-1],
        rules=Rules(min_grade=None),
        portfolio=Portfolio(slippage_pct=slippage, **portfolio),
        exits=exits,
    )


def book(
    bars: dict[int, list[tuple[float, float, float, float]]],
    sma50: dict[int, list[float]] | None = None,
) -> PriceBook:
    """`bars[ticker]` = (open, high, low, close) from DAYS[0]; NaN after its last bar."""
    n = max(len(b) for b in bars.values())

    def arr(values: list[float]) -> np.ndarray:
        return np.array(values + [NAN] * (n - len(values)), dtype=np.float64)

    return PriceBook(
        DAYS[:n],
        {t: arr([b[0] for b in v]) for t, v in bars.items()},
        {t: arr([b[1] for b in v]) for t, v in bars.items()},
        {t: arr([b[2] for b in v]) for t, v in bars.items()},
        {t: arr([b[3] for b in v]) for t, v in bars.items()},
        {t: arr((sma50 or {}).get(t, [])) for t in bars},
        {t: arr([]) for t in bars},
        {t: f"T{t}" for t in bars},
    )


def candidate(ticker: int = 1, day: int = 0, **kw: object) -> Candidate:
    values: dict[str, object] = {
        "date": DAYS[day],
        "ticker_id": ticker,
        "setup": 1,
        "pattern": "vcp",
        "state": "near_pivot",
        "pivot": 50.0,
        "entry": 50.10,
        "stop": 47.00,
        "score": 85.0,
        "grade": "A",
        "readiness_pct": 1.0,
        "regime": "confirmed_uptrend",
    }
    values.update(kw)
    return Candidate(**values)  # type: ignore[arg-type]


FLAT = (49.0, 49.5, 48.5, 49.0)


def test_buy_stop_fills_at_the_entry_and_sizes_by_risk() -> None:
    # Day 1 opens 49.80 under the 50.10 entry and trades up to 51: filled at 50.10.
    # Risk 1% of 100,000 = 1,000 over 50.10 - 47.00 = 3.10 a share → 322 shares (16,132.20,
    # under the 25% cap). Day 2 trades through the 47 stop → sold at 47: -3.10 × 322.
    bars = {1: [FLAT, (49.8, 51.0, 49.6, 50.6), (49.0, 49.2, 46.5, 46.8)]}
    sim = simulate(params(), [candidate()], set(), book(bars))
    [t] = sim.trades
    assert (t.entry_date, t.entry_price, t.shares) == (DAYS[1], 50.10, 322)
    assert t.cost == pytest.approx(16_132.20)
    assert (t.exit_date, t.exit_price, t.exit_reason) == (DAYS[2], 47.0, "Stop")
    assert t.pnl == pytest.approx(-998.20)
    assert t.r_multiple == pytest.approx(-1.0)
    assert sim.equity == pytest.approx([100_000, 100_000 + 322 * (50.6 - 50.10), 100_000 - 998.20])


def test_a_gap_inside_the_buy_zone_fills_at_the_open_and_above_it_is_skipped() -> None:
    # Ticker 1 opens at 51.00 (inside 50.00-52.50): filled at the open. Ticker 2 opens at
    # 53.00, above the zone top 52.50: skipped. Ticker 3 never reaches its entry: no order.
    bars = {
        1: [FLAT, (51.0, 51.5, 50.8, 51.2)],
        2: [FLAT, (53.0, 54.0, 52.8, 53.5)],
        3: [FLAT, (49.0, 49.9, 48.8, 49.5)],
    }
    cands = [candidate(1), candidate(2), candidate(3)]
    sim = simulate(params(), cands, set(), book(bars))
    assert [(t.ticker_id, t.entry_price) for t in sim.trades] == [(1, 51.0)]
    assert sim.counts["gapped_above_zone"] == 1
    assert sim.trades[0].exit_reason == "End of test"  # marked at the last close, no costs
    assert sim.trades[0].exit_price == 51.2


def test_slippage_and_commission_worsen_every_fill() -> None:
    # Fill 50.10 × 1.001 = 50.1501; 1,000 / 3.1501 → 317 shares; cost 317 × 50.1501 + 5.
    # Stop sale: 47 × 0.999 = 46.953, proceeds 317 × 46.953 - 5.
    bars = {1: [FLAT, (49.8, 51.0, 49.6, 50.6), (49.0, 49.2, 46.5, 46.8)]}
    sim = simulate(params(slippage=0.1, commission=5), [candidate()], set(), book(bars))
    [t] = sim.trades
    assert t.entry_price == pytest.approx(50.1501)
    assert t.shares == 317
    assert t.cost == pytest.approx(317 * 50.1501 + 5)
    assert t.exit_price == pytest.approx(46.953)
    assert t.pnl == pytest.approx(317 * 46.953 - 5 - (317 * 50.1501 + 5))


def test_stop_gaps_and_the_entry_day_worst_case() -> None:
    # Ticker 1 gaps below its stop on day 2: sold at the 45.00 open, not the 47.00 stop.
    # Ticker 2 fills on day 1 but its low that day (46.90) is under the stop: we can't know
    # the order of prices within the day, so it counts as stopped at 47.
    bars = {
        1: [FLAT, (49.8, 51.0, 49.6, 50.6), (45.0, 46.0, 44.0, 45.5)],
        2: [FLAT, (49.8, 51.0, 46.9, 50.0), (50, 50, 50, 50)],
    }
    sim = simulate(params(), [candidate(1), candidate(2, setup=2)], set(), book(bars))
    by = {t.ticker_id: t for t in sim.trades}
    assert (by[1].exit_price, by[1].exit_reason) == (45.0, "Stop (gapped below)")
    assert (by[2].exit_date, by[2].exit_price, by[2].exit_reason) == (DAYS[1], 47.0, "Stop")


def test_partial_profit_breakeven_and_the_trailing_average() -> None:
    # 322 shares at 50.10 (1R = 3.10). Day 2 closes 56.50 ≥ 50.10 + 2R = 56.30: the stop
    # moves to 50.10 from day 3. Day 3's high 60.20 ≥ +20% (60.12): a third (107 of 322,
    # rounded) is sold at 60.12. Day 4 closes 57.00 under its 50-day SMA (57.50): the other
    # 215 are sold at the close.
    exits = PLAIN.model_copy(
        update={"partial_fraction_pct": 33.33, "breakeven_r": 2, "trailing": "sma50"}
    )
    bars = {
        1: [
            FLAT,
            (49.8, 51.0, 49.6, 50.6),
            (51.0, 56.8, 50.9, 56.5),
            (57.0, 60.2, 56.8, 59.0),
            (58.0, 58.5, 56.5, 57.0),
        ]
    }
    sma = {1: [NAN, 45.0, 46.0, 47.0, 57.5]}
    sim = simulate(params(exits), [candidate()], set(), book(bars, sma))
    [t] = sim.trades
    assert (t.breakeven, t.current_stop) == (True, 50.10)
    assert [(f.date, f.price, f.shares, f.reason) for f in t.exits] == [
        (DAYS[3], 60.12, 107, "Partial profit (+20%)"),
        (DAYS[4], 57.0, 215, "Closed below the 50-day SMA"),
    ]
    assert t.pnl == pytest.approx(107 * 60.12 + 215 * 57.0 - 322 * 50.10)


def test_breakeven_stop_and_the_ten_percent_trigger() -> None:
    # A wide stop (40.00, 1R = 10.10) so 2R needs 70.30; the +10% rule (55.11) moves the stop
    # to breakeven first. Day 3 trades down through 50.10: out at breakeven.
    exits = PLAIN.model_copy(update={"breakeven_r": 2, "breakeven_gain_pct": 10})
    bars = {1: [FLAT, (49.8, 51.0, 49.6, 50.6), (51.0, 55.5, 50.9, 55.2), (54.0, 54.0, 49.5, 50.0)]}
    sim = simulate(params(exits), [candidate(stop=40.0)], set(), book(bars))
    [t] = sim.trades
    assert t.exit_price == pytest.approx(50.10)
    assert t.exit_reason == "Breakeven stop"
    assert t.pnl == pytest.approx(0)


def test_time_stop_after_fifteen_sessions_without_progress() -> None:
    # Filled at 50.10 on day 1, then it closes at 51.00 for 15 sessions: under +5% (52.605)
    # at the 15th session after the entry day → sold at that close.
    exits = PLAIN.model_copy(update={"time_stop_sessions": 15, "time_stop_min_gain_pct": 5})
    bars = {1: [FLAT, (49.8, 51.0, 49.6, 50.6)] + [(51.0, 51.2, 50.8, 51.0)] * 20}
    sim = simulate(params(exits), [candidate()], set(), book(bars))
    [t] = sim.trades
    assert (t.exit_date, t.sessions) == (DAYS[16], 15)
    assert t.exit_reason == "Time stop: less than +5% after 15 sessions"


def test_an_unconfirmed_breakout_is_sold_at_the_close() -> None:
    # Both fill on day 1; only ticker 2's breakout was confirmed at the close (the tape).
    exits = PLAIN.model_copy(update={"sell_unconfirmed": True})
    bars = {1: [FLAT, (49.8, 51.0, 49.6, 50.3)], 2: [FLAT, (49.8, 51.0, 49.6, 50.9), FLAT]}
    sim = simulate(params(exits), [candidate(1), candidate(2)], {(DAYS[1], 2)}, book(bars))
    by = {t.ticker_id: t for t in sim.trades}
    assert (by[1].exit_date, by[1].exit_price) == (DAYS[1], 50.3)
    assert by[1].exit_reason == "Breakout not confirmed at the close"
    assert by[2].exit_date == DAYS[2]


def test_slots_go_to_the_best_scores_and_cash_runs_out() -> None:
    # Two slots, three orders: scores 70, 90, 80 → tickers 2 and 3 fill; 1 has no slot.
    bars = {t: [FLAT, (49.8, 51.0, 49.6, 50.6)] for t in (1, 2, 3)}
    cands = [candidate(1, score=70.0), candidate(2, score=90.0), candidate(3, score=80.0)]
    sim = simulate(params(max_positions=2), cands, set(), book(bars))
    assert [t.ticker_id for t in sim.trades] == [2, 3]
    assert sim.counts["no_slot"] == 1
    # A 1% risk on a 0.10 stop would want 10,000 shares; the 25% cap allows 499 (24,999.90).
    # Four of those leave 0.40 in cash: the fifth order finds no cash.
    tight = [candidate(t, stop=50.0, setup=t) for t in range(1, 6)]
    bars = {t: [FLAT, (49.8, 51.0, 49.6, 50.6)] for t in range(1, 6)}
    sim = simulate(params(), tight, set(), book(bars))
    assert [t.shares for t in sim.trades] == [499, 499, 499, 499]
    assert sim.counts["no_cash"] == 1


def test_rules_filter_grades_patterns_risk_and_regime() -> None:
    bars = {t: [FLAT, (49.8, 51.0, 49.6, 50.6)] for t in range(1, 6)}
    cands = [
        candidate(1, grade="B"),
        candidate(2, pattern="flat_base"),
        candidate(3, risk_too_wide=True),
        candidate(4, regime="correction"),
        candidate(5),
    ]
    rules = Rules(min_grade="A", patterns=["vcp"], skip_risk_too_wide=True, skip_correction=True)
    p = params().model_copy(update={"rules": rules})
    sim = simulate(p, cands, set(), book(bars))
    assert [t.ticker_id for t in sim.trades] == [5]
    # A saved screen narrows it further, on the screener fields.
    fields = {"rs_rating": 95}
    sim = simulate(
        params(),
        [candidate(1, fields=fields), candidate(2, fields={"rs_rating": 60})],
        set(),
        book({1: bars[1], 2: bars[2]}),
        matches=lambda f: f.get("rs_rating", 0) >= 90,
    )
    assert [t.ticker_id for t in sim.trades] == [1]


def test_a_setup_is_traded_once() -> None:
    # Stopped out on day 2; the same setup is still a candidate on days 1-3 but isn't bought
    # again. A new setup (2) on day 3 is.
    bars = {
        1: [FLAT, (49.8, 51.0, 49.6, 50.6), (49.0, 49.2, 46.5, 46.8), FLAT, (49.8, 51, 49.6, 50.6)]
    }
    cands = [candidate(day=0), candidate(day=1), candidate(day=2), candidate(day=3, setup=2)]
    sim = simulate(params(), cands, set(), book(bars))
    assert [(t.setup, t.entry_date) for t in sim.trades] == [(1, DAYS[1]), (2, DAYS[4])]


# --- Metrics ---------------------------------------------------------------------------------


def test_drawdown_and_equity_metrics_by_hand() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2025, 1, 2)]
    equity = [100.0, 110.0, 99.0, 121.0]
    assert drawdowns(equity) == pytest.approx([0, 0, -10, 0])
    m = equity_metrics(days, equity)
    # +21% over 366 days = 1.00205 years: CAGR = exp(ln 1.21 / 1.00205) - 1
    # = exp(0.19062 / 1.00205) - 1 = exp(0.19023) - 1 = 20.95%.
    assert m["total_return_pct"] == 21.0
    assert m["cagr_pct"] == 20.95
    assert (m["max_drawdown_pct"], m["max_drawdown_peak"], m["max_drawdown_trough"]) == (
        -10.0,
        "2024-01-03",
        "2024-01-04",
    )
    # Daily returns +10%, -10%, +22.22%: mean 7.407%, population sd 13.08%.
    r = [0.10, -0.10, 121 / 99 - 1]
    mean = sum(r) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 3)
    assert m["sharpe"] == pytest.approx(round(mean / sd * math.sqrt(252), 2))
    downside = math.sqrt(0.01 / 3)
    assert m["sortino"] == pytest.approx(round(mean / downside * math.sqrt(252), 2))


def _trade(pnl: float, cost: float = 1000) -> Trade:
    """100 shares bought at 10.00 with the stop at 9.00: 1R = 100."""
    t = Trade(
        1, "T", 1, "vcp", "A", 80, "confirmed_uptrend", DAYS[0], DAYS[1], 10.0, 9.0, 100, cost
    )
    t.proceeds = cost + pnl
    return t


def test_trade_metrics_by_hand() -> None:
    # Risk 100 each (100 shares × 1.00). P&L +300, +100, -100, -50 on 1,000 cost each.
    trades = [_trade(300), _trade(100), _trade(-100), _trade(-50)]
    m = trade_metrics(trades)
    assert (m["trades"], m["wins"], m["losses"], m["win_rate_pct"]) == (4, 2, 2, 50.0)
    assert (m["avg_win_pct"], m["avg_loss_pct"]) == (20.0, -7.5)
    assert m["payoff_ratio"] == pytest.approx(2.67)
    assert m["expectancy_r"] == pytest.approx((3 + 1 - 1 - 0.5) / 4, abs=0.01)
    assert m["profit_factor"] == pytest.approx(400 / 150, abs=0.01)
    assert m["net_profit"] == 250
    rows = breakdown(trades, lambda t: t.pattern)
    assert (rows[0]["key"], rows[0]["trades"]) == ("vcp", 4)
    assert trade_metrics([])["trades"] == 0

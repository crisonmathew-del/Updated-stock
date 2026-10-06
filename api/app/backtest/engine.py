"""Stage 2 of the backtest: trade the tape's candidates with a portfolio, one session at a time.

Pure: the candidates, the confirmed breakouts and the prices come in; trades and the daily
equity curve come out. Every rule works on what is known by then: an order placed after
session t-1's close is filled (or not) on session t; close-based exits use session t's close.

Each session, in order:
1. Orders: yesterday's candidates that pass the rules (grade, pattern, risk, regime, saved
   screen), best score first, for stocks not held and setups not traded before, while a slot
   is free. A buy-stop at the plan's entry: if the stock opens at or above it inside the buy
   zone it fills at the open; if it opens above the buy zone the trade is skipped; otherwise
   it fills at the entry if the day's high reaches it.
   Size: risk % of the previous close's equity ÷ (fill - stop), at most the maximum position
   % of equity, and no more than the cash on hand. Slippage worsens every fill.
2. Stops: a stock opening at or below its stop is sold at the open; otherwise one trading
   through it is sold at the stop. On the entry day a low at or below the stop counts as
   stopped out (the order of prices within a day is unknown, so assume the worse case).
3. Partial profit: once the high reaches the profit target (+20% by default) a part (a third
   by default) is sold there. A day that also hit the stop counts as stopped first.
4. At the close: an entry whose breakout wasn't confirmed at the close (the cell's volume and
   close-in-range rules, from the tape) is sold (optional); a close below the trailing average
   (50-day by default) sells; after `time_stop_sessions` a close less than
   `time_stop_min_gain_pct` above the entry sells; a close 2R or 10% above the entry moves the
   stop to the entry (breakeven) from the next session.
5. Equity: cash plus every holding at its latest close.
Positions still open after the last session are marked at its close ("end of test", no costs).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from app.settings.schema import AppSettings

Floats = npt.NDArray[np.float64]
GRADE_RANK = {"A+": 4, "A": 3, "B": 2, "C": 1}
TRAILS = {"sma50": "50-day SMA", "ema21": "21-day EMA"}


class Rules(BaseModel):
    """Which candidates to trade."""

    min_grade: Literal["A+", "A", "B", "C"] | None = "A"
    patterns: list[str] = Field(default_factory=list)  # empty: every pattern
    near_pivot_only: bool = False
    skip_risk_too_wide: bool = True
    skip_correction: bool = False
    screen_id: int | None = None
    screen_name: str | None = None
    screen_filters: list[dict[str, Any]] = Field(default_factory=list)


class Portfolio(BaseModel):
    initial_capital: float = Field(100_000, gt=0)
    risk_pct: float = Field(1.0, gt=0, le=10)
    max_position_pct: float = Field(25, gt=0, le=100)
    max_positions: int = Field(10, ge=1, le=100)
    slippage_pct: float = Field(0.1, ge=0, le=5)
    commission: float = Field(0, ge=0)  # per order, in the account currency


class Exits(BaseModel):
    sell_unconfirmed: bool = True
    trailing: Literal["sma50", "ema21", "none"] = "sma50"
    time_stop_sessions: int = Field(15, ge=0, le=250)  # 0: no time stop
    time_stop_min_gain_pct: float = Field(5, ge=0)
    partial_profit_pct: float = Field(20, gt=0)
    partial_fraction_pct: float = Field(33.33, ge=0, le=100)  # 0: no partial sale
    breakeven_r: float = Field(2, gt=0)
    breakeven_gain_pct: float = Field(10, gt=0)


class BacktestParams(BaseModel):
    start: date
    end: date
    rules: Rules = Field(default_factory=Rules)
    portfolio: Portfolio = Field(default_factory=Portfolio)
    exits: Exits = Field(default_factory=Exits)
    buy_zone_pct: float = 5
    in_sample_pct: float = Field(70, gt=0, lt=100)
    sensitivity: bool = False

    @classmethod
    def defaults(cls, settings: AppSettings, start: date, end: date) -> "BacktestParams":
        s = settings
        return cls(
            start=start,
            end=end,
            rules=Rules(
                min_grade=s.backtest_min_grade,
                skip_risk_too_wide=s.backtest_skip_risk_too_wide,
                skip_correction=s.backtest_skip_correction,
            ),
            portfolio=Portfolio(
                initial_capital=s.account_size,
                risk_pct=s.risk_per_trade_pct,
                max_position_pct=s.max_position_pct,
                max_positions=s.backtest_max_positions,
                slippage_pct=s.backtest_slippage_pct,
                commission=s.backtest_commission,
            ),
            exits=Exits(
                sell_unconfirmed=s.backtest_sell_unconfirmed,
                trailing=s.backtest_trailing_exit,
                time_stop_sessions=s.backtest_time_stop_sessions,
                time_stop_min_gain_pct=s.backtest_time_stop_min_gain_pct,
                partial_profit_pct=s.profit_take_min_pct,
                partial_fraction_pct=s.backtest_partial_fraction_pct,
                breakeven_r=s.breakeven_after_r,
                breakeven_gain_pct=s.breakeven_after_gain_pct,
            ),
            buy_zone_pct=s.buy_zone_max_pct_above_pivot,
            in_sample_pct=s.backtest_in_sample_pct,
        )


@dataclass(frozen=True)
class Candidate:
    """A tape candidate: an order for the session after `date`."""

    date: date
    ticker_id: int
    setup: int
    pattern: str | None
    state: str
    pivot: float
    entry: float
    stop: float
    score: float | None
    grade: str | None
    readiness_pct: float | None
    regime: str | None
    risk_too_wide: bool = False
    fields: dict[str, Any] | None = None  # screener fields, for saved screens


@dataclass
class PriceBook:
    """Daily prices aligned to `sessions` (NaN where a stock has no bar)."""

    sessions: list[date]
    open: dict[int, Floats]
    high: dict[int, Floats]
    low: dict[int, Floats]
    close: dict[int, Floats]
    sma50: dict[int, Floats]
    ema21: dict[int, Floats]
    symbols: dict[int, str] = field(default_factory=dict)


@dataclass
class Fill:
    date: date
    price: float
    shares: int
    reason: str


@dataclass
class Trade:
    ticker_id: int
    symbol: str
    setup: int
    pattern: str | None
    grade: str | None
    score: float | None
    regime: str | None
    signal_date: date
    entry_date: date
    entry_price: float  # the fill, slippage included
    stop: float  # the plan's stop at entry
    shares: int
    cost: float  # shares × fill + commission
    exits: list[Fill] = field(default_factory=list)
    proceeds: float = 0.0
    sessions: int = 0  # sessions held after the entry day
    current_stop: float = 0.0
    shares_left: int = 0
    partial_done: bool = False
    breakeven: bool = False

    @property
    def closed(self) -> bool:
        return self.shares_left == 0

    @property
    def exit_date(self) -> date | None:
        return self.exits[-1].date if self.exits else None

    @property
    def exit_price(self) -> float | None:
        sold = sum(f.shares for f in self.exits)
        return sum(f.price * f.shares for f in self.exits) / sold if sold else None

    @property
    def exit_reason(self) -> str | None:
        return self.exits[-1].reason if self.exits else None

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.cost * 100

    @property
    def risk(self) -> float:
        """Dollars at risk at entry (1R)."""
        return self.shares * (self.entry_price - self.stop)

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk if self.risk > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker_id": self.ticker_id,
            "symbol": self.symbol,
            "setup": self.setup,
            "pattern": self.pattern,
            "grade": self.grade,
            "score": None if self.score is None else round(self.score, 1),
            "regime": self.regime,
            "signal_date": self.signal_date.isoformat(),
            "entry_date": self.entry_date.isoformat(),
            "entry_price": round(self.entry_price, 4),
            "stop": round(self.stop, 4),
            "shares": self.shares,
            "exit_date": None if self.exit_date is None else self.exit_date.isoformat(),
            "exit_price": None if self.exit_price is None else round(self.exit_price, 4),
            "exit_reason": self.exit_reason,
            "partial": self.partial_done,
            "exits": [
                {
                    "date": f.date.isoformat(),
                    "price": round(f.price, 4),
                    "shares": f.shares,
                    "reason": f.reason,
                }
                for f in self.exits
            ],
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "r": round(self.r_multiple, 2),
            "sessions": self.sessions,
        }


@dataclass
class Simulation:
    trades: list[Trade]
    dates: list[date]
    equity: list[float]
    invested: list[float]
    positions: list[int]
    counts: dict[str, int]


def passes(c: Candidate, rules: Rules, matches: Callable[[dict[str, Any]], bool] | None) -> bool:
    """The rule set's filters on one candidate."""
    if (
        rules.min_grade is not None
        and GRADE_RANK.get(c.grade or "", 0) < GRADE_RANK[rules.min_grade]
    ):
        return False
    if rules.patterns and (c.pattern or "") not in rules.patterns:
        return False
    if rules.near_pivot_only and c.state != "near_pivot":
        return False
    if rules.skip_risk_too_wide and c.risk_too_wide:
        return False
    if rules.skip_correction and c.regime == "correction":
        return False
    return not (matches is not None and not matches(c.fields or {}))


def _rank(c: Candidate) -> tuple[float, float, int]:
    readiness = c.readiness_pct if c.readiness_pct is not None else math.inf
    return (-(c.score if c.score is not None else -math.inf), readiness, c.ticker_id)


def simulate(
    params: BacktestParams,
    candidates: list[Candidate],
    breakouts: set[tuple[date, int]],
    prices: PriceBook,
    matches: Callable[[dict[str, Any]], bool] | None = None,
) -> Simulation:
    """Trade `candidates` over `prices.sessions` (see the module docstring)."""
    p, x = params.portfolio, params.exits
    slip = p.slippage_pct / 100
    sessions = prices.sessions
    orders: dict[date, list[Candidate]] = {}
    for c in candidates:
        if passes(c, params.rules, matches):
            orders.setdefault(c.date, []).append(c)
    cash = p.initial_capital
    held: dict[int, Trade] = {}
    trades: list[Trade] = []
    traded: set[tuple[int, int]] = set()
    counts = {"orders": 0, "filled": 0, "gapped_above_zone": 0, "no_slot": 0, "no_cash": 0}
    last_close: dict[int, float] = {}
    out = Simulation(trades, [], [], [], [], counts)
    equity = p.initial_capital

    def sell(
        trade: Trade, day: date, price: float, shares: int, reason: str, costs: bool = True
    ) -> None:
        nonlocal cash
        fill = price * (1 - slip) if costs else price
        proceeds = shares * fill - (p.commission if costs else 0)
        cash += proceeds
        trade.proceeds += proceeds
        trade.shares_left -= shares
        trade.exits.append(Fill(day, fill, shares, reason))
        if trade.closed:
            held.pop(trade.ticker_id, None)

    for t, day in enumerate(sessions):
        # 1. Entries from yesterday's candidates.
        if t > 0:
            for c in sorted(orders.get(sessions[t - 1], []), key=_rank):
                counts["orders"] += 1
                if c.ticker_id in held or (c.ticker_id, c.setup) in traded:
                    continue
                if len(held) >= p.max_positions:
                    counts["no_slot"] += 1
                    continue
                o, h = _at(prices.open, c.ticker_id, t), _at(prices.high, c.ticker_id, t)
                if o is None or h is None:
                    continue
                zone_top = c.pivot * (1 + params.buy_zone_pct / 100)
                if o >= c.entry:
                    if o > zone_top:
                        counts["gapped_above_zone"] += 1
                        continue
                    raw = o
                elif h >= c.entry:
                    raw = c.entry
                else:
                    continue
                fill = raw * (1 + slip)
                if fill <= c.stop:
                    continue
                shares = math.floor(equity * p.risk_pct / 100 / (fill - c.stop))
                shares = min(shares, math.floor(equity * p.max_position_pct / 100 / fill))
                shares = min(shares, math.floor((cash - p.commission) / fill))
                if shares < 1:
                    counts["no_cash"] += 1
                    continue
                cost = shares * fill + p.commission
                cash -= cost
                trade = Trade(
                    c.ticker_id,
                    prices.symbols.get(c.ticker_id, str(c.ticker_id)),
                    c.setup,
                    c.pattern,
                    c.grade,
                    c.score,
                    c.regime,
                    c.date,
                    day,
                    fill,
                    c.stop,
                    shares,
                    cost,
                    current_stop=c.stop,
                    shares_left=shares,
                )
                held[c.ticker_id] = trade
                trades.append(trade)
                traded.add((c.ticker_id, c.setup))
                counts["filled"] += 1

        # 2-4. Exits for everything held (including today's entries).
        for trade in list(held.values()):
            tid = trade.ticker_id
            o, h, lo, cl = (
                _at(prices.open, tid, t),
                _at(prices.high, tid, t),
                _at(prices.low, tid, t),
                _at(prices.close, tid, t),
            )
            if o is None or h is None or lo is None or cl is None:
                continue
            entry_day = trade.entry_date == day
            if not entry_day:
                trade.sessions += 1
            stop = trade.current_stop
            label = "Breakeven stop" if trade.breakeven else "Stop"
            if not entry_day and o <= stop:
                sell(trade, day, o, trade.shares_left, f"{label} (gapped below)")
                continue
            if lo <= stop:
                sell(trade, day, stop, trade.shares_left, label)
                continue
            target = trade.entry_price * (1 + x.partial_profit_pct / 100)
            if x.partial_fraction_pct > 0 and not trade.partial_done and h >= target:
                part = min(
                    trade.shares_left - 1, round(trade.shares * x.partial_fraction_pct / 100)
                )
                if part >= 1:
                    price = o if (not entry_day and o >= target) else target
                    sell(trade, day, price, part, f"Partial profit (+{x.partial_profit_pct:g}%)")
                trade.partial_done = True
            if entry_day and x.sell_unconfirmed and (day, tid) not in breakouts:
                sell(trade, day, cl, trade.shares_left, "Breakout not confirmed at the close")
                continue
            if not entry_day and x.trailing != "none":
                average = _at(prices.sma50 if x.trailing == "sma50" else prices.ema21, tid, t)
                if average is not None and cl < average:
                    sell(
                        trade, day, cl, trade.shares_left, f"Closed below the {TRAILS[x.trailing]}"
                    )
                    continue
            if (
                x.time_stop_sessions
                and trade.sessions == x.time_stop_sessions
                and cl < trade.entry_price * (1 + x.time_stop_min_gain_pct / 100)
            ):
                sell(
                    trade,
                    day,
                    cl,
                    trade.shares_left,
                    f"Time stop: less than +{x.time_stop_min_gain_pct:g}% after "
                    f"{x.time_stop_sessions} sessions",
                )
                continue
            one_r = trade.entry_price - trade.stop
            if not trade.breakeven and (
                cl >= trade.entry_price + x.breakeven_r * one_r
                or cl >= trade.entry_price * (1 + x.breakeven_gain_pct / 100)
            ):
                trade.breakeven = True
                trade.current_stop = max(trade.current_stop, trade.entry_price)

        # 5. Equity at the close.
        invested = 0.0
        for trade in held.values():
            cl = _at(prices.close, trade.ticker_id, t)
            if cl is not None:
                last_close[trade.ticker_id] = cl
            invested += trade.shares_left * last_close.get(trade.ticker_id, trade.entry_price)
        equity = cash + invested
        out.dates.append(day)
        out.equity.append(equity)
        out.invested.append(invested)
        out.positions.append(len(held))

    if sessions:
        last = sessions[-1]
        for trade in list(held.values()):
            price = last_close.get(trade.ticker_id, trade.entry_price)
            sell(trade, last, price, trade.shares_left, "End of test", costs=False)
    return out


def _at(series: dict[int, Floats], ticker_id: int, t: int) -> float | None:
    values = series.get(ticker_id)
    if values is None:
        return None
    v = float(values[t])
    return None if math.isnan(v) else v

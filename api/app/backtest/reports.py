"""The backtest report: headline metrics, the equity curve against SPY, drawdown, breakdowns,
in-sample against out-of-sample, the sensitivity heatmap and the labels every report carries.
Pure: the simulation and the benchmark's closes come in, JSON-ready dicts come out."""

import math
from collections.abc import Sequence
from datetime import date
from typing import Any

from app.backtest.engine import BacktestParams, Simulation, Trade
from app.backtest.metrics import (
    breakdown,
    drawdowns,
    equity_metrics,
    exposure_pct,
    trade_metrics,
)

HYPOTHETICAL = (
    "Hypothetical results: simulated trades on past prices, not real trading. They leave out "
    "taxes, borrowing costs and the price impact of large orders, and past results don't "
    "predict future ones."
)
SURVIVORSHIP = (
    "Survivorship bias: the free price data covers only stocks listed today. Companies that "
    "were delisted during the period (bankrupt, acquired, merged) are missing, and their "
    "losses with them, so these results are better than the strategy would have done. A paid "
    "data source with delisted stocks removes this bias."
)


def assumptions(params: BacktestParams) -> list[str]:
    x, p = params.exits, params.portfolio
    out = [
        "Each order is a buy-stop at the plan's entry for the next session, placed after the "
        "close the setup qualified on. A gap inside the buy zone fills at the open; a gap above "
        f"it (more than {params.buy_zone_pct:g}% over the pivot) is skipped.",
        "On the entry day a low at or below the stop counts as stopped out: daily bars don't "
        "say whether the low came before the fill, so the worse case is assumed.",
        f"Slippage {p.slippage_pct:g}% on every fill, commission {p.commission:g} per order; "
        f"risk {p.risk_pct:g}% of equity per trade, positions at most "
        f"{p.max_position_pct:g}% of equity, at most {p.max_positions} at once, no margin.",
        "Signals and setups come from the same rules the nightly scan runs, evaluated with only "
        "what was known at each close (fundamentals by filing date, indicators, group ranks "
        "and the market regime of that day).",
    ]
    if x.sell_unconfirmed:
        out.append(
            "An entry whose breakout isn't confirmed at the close (volume or close in the "
            "day's range below the thresholds) is sold at that close."
        )
    return out


def split_index(sessions: int, in_sample_pct: float) -> int:
    """The first out-of-sample session's index."""
    return max(1, min(sessions - 1, math.floor(sessions * in_sample_pct / 100)))


def _segment(
    dates: Sequence[date],
    equity: Sequence[float],
    invested: Sequence[float],
    trades: Sequence[Trade],
) -> dict[str, Any]:
    return {
        "start": dates[0].isoformat() if dates else None,
        "end": dates[-1].isoformat() if dates else None,
        **equity_metrics(dates, equity),
        "exposure_pct": exposure_pct(invested, equity),
        **trade_metrics(trades),
    }


def headline(summary: dict[str, Any]) -> dict[str, Any]:
    """The numbers a run list shows."""
    keys = (
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe",
        "trades",
        "win_rate_pct",
        "expectancy_r",
        "profit_factor",
        "benchmark_cagr_pct",
    )
    return {k: summary.get(k) for k in keys}


def heatmap_cell(sim: Simulation) -> dict[str, Any]:
    eq = equity_metrics(sim.dates, sim.equity)
    tr = trade_metrics(sim.trades)
    return {
        "cagr_pct": eq["cagr_pct"],
        "max_drawdown_pct": eq["max_drawdown_pct"],
        "trades": tr["trades"],
        "win_rate_pct": tr["win_rate_pct"],
        "expectancy_r": tr["expectancy_r"],
        "profit_factor": tr["profit_factor"],
    }


def build_report(
    params: BacktestParams,
    sim: Simulation,
    benchmark: Sequence[float | None],
    *,
    tape: dict[str, Any],
    signals: dict[str, int],
    heatmap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the lab shows for one run. `benchmark` is SPY's close per session (None
    where missing)."""
    dates, equity = sim.dates, sim.equity
    capital = params.portfolio.initial_capital
    first = next((b for b in benchmark if b), None)
    scaled: list[float | None] = []
    last: float | None = None
    for b in benchmark:
        last = b if b else last
        scaled.append(None if first is None or last is None else capital * last / first)
    bench_values = [v for v in scaled if v is not None]
    bench_dates = [d for d, v in zip(dates, scaled, strict=True) if v is not None]
    bench = equity_metrics(bench_dates, bench_values)

    cut = split_index(len(dates), params.in_sample_pct) if dates else 0
    split_date = dates[cut] if dates else None
    inside = [t for t in sim.trades if split_date is None or t.entry_date < split_date]
    outside = [t for t in sim.trades if split_date is not None and t.entry_date >= split_date]
    summary = {
        **equity_metrics(dates, equity),
        "exposure_pct": exposure_pct(sim.invested, equity),
        **trade_metrics(sim.trades),
        "benchmark_total_return_pct": bench["total_return_pct"],
        "benchmark_cagr_pct": bench["cagr_pct"],
        "benchmark_max_drawdown_pct": bench["max_drawdown_pct"],
    }
    trades = []
    for n, t in enumerate(sim.trades, start=1):
        row = t.as_dict()
        row["n"] = n
        row["sample"] = "in" if split_date is None or t.entry_date < split_date else "out"
        trades.append(row)
    return {
        "hypothetical": True,
        "labels": {"hypothetical": HYPOTHETICAL, "survivorship": SURVIVORSHIP},
        "assumptions": assumptions(params),
        "period": {
            "start": dates[0].isoformat() if dates else None,
            "end": dates[-1].isoformat() if dates else None,
            "sessions": len(dates),
            "split": None if split_date is None else split_date.isoformat(),
        },
        "summary": summary,
        "equity": {
            "dates": [d.isoformat() for d in dates],
            "equity": [round(v, 2) for v in equity],
            "benchmark": [None if v is None else round(v, 2) for v in scaled],
            "drawdown": [round(v, 2) for v in drawdowns(equity)],
            "positions": sim.positions,
            "exposure": [
                round(i / e * 100, 1) if e > 0 else 0.0
                for i, e in zip(sim.invested, equity, strict=True)
            ],
        },
        "samples": {
            "split_pct": params.in_sample_pct,
            "in": _segment(dates[: cut + 1], equity[: cut + 1], sim.invested[: cut + 1], inside),
            "out": _segment(dates[cut:], equity[cut:], sim.invested[cut:], outside),
        },
        "by_regime": breakdown(sim.trades, lambda t: t.regime),
        "by_pattern": breakdown(sim.trades, lambda t: t.pattern),
        "by_grade": breakdown(sim.trades, lambda t: t.grade),
        "by_exit": breakdown(sim.trades, lambda t: _exit_kind(t.exit_reason)),
        "by_year": breakdown(sim.trades, lambda t: str(t.entry_date.year)),
        "orders": sim.counts,
        "signals": signals,
        "tape": tape,
        "heatmap": heatmap,
        "trades": trades,
    }


def _exit_kind(reason: str | None) -> str:
    """Exit reasons without their numbers, for grouping."""
    if reason is None:
        return "open"
    for prefix in ("Time stop", "Closed below", "Partial profit"):
        if reason.startswith(prefix):
            return reason.split(":")[0] if prefix == "Time stop" else reason
    return reason

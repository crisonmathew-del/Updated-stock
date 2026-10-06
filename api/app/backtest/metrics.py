"""Backtest metrics (spec §11), pure functions over the daily equity curve and the trades.

Definitions:
- Total return and CAGR from the first to the last equity value; CAGR annualises over
  calendar days (365.25 per year).
- Maximum drawdown: the largest fall from a running peak, in % of that peak.
- Sharpe and Sortino: mean daily return ÷ the standard deviation of daily returns (Sortino:
  of the negative ones, as root mean square over all days), × √252; no risk-free rate.
- Exposure: the average share of equity invested at the close.
- A trade wins when its profit after costs is above zero. Payoff ratio: average winning % ÷
  average losing % (absolute). Expectancy: the average R multiple (profit ÷ dollars at risk at
  entry). Profit factor: gross profit ÷ gross loss.
"""

import math
import statistics
from collections.abc import Callable, Sequence
from datetime import date
from itertools import pairwise
from typing import Any

from app.backtest.engine import Trade

TRADING_DAYS = 252


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def drawdowns(equity: Sequence[float]) -> list[float]:
    """Drawdown at each point, in % below the running peak (0 or negative)."""
    peak = -math.inf
    out = []
    for v in equity:
        peak = max(peak, v)
        out.append((v / peak - 1) * 100 if peak > 0 else 0.0)
    return out


def equity_metrics(dates: Sequence[date], equity: Sequence[float]) -> dict[str, Any]:
    if len(equity) < 2:
        return {
            "start_equity": equity[0] if equity else None,
            "end_equity": equity[-1] if equity else None,
            "total_return_pct": None,
            "cagr_pct": None,
            "max_drawdown_pct": None,
            "max_drawdown_peak": None,
            "max_drawdown_trough": None,
            "sharpe": None,
            "sortino": None,
            "volatility_pct": None,
        }
    first, last = equity[0], equity[-1]
    years = (dates[-1] - dates[0]).days / 365.25
    total = last / first - 1
    cagr = (last / first) ** (1 / years) - 1 if years > 0 and last > 0 else None
    dd = drawdowns(equity)
    trough = min(range(len(dd)), key=lambda i: dd[i])
    peak = max(range(trough + 1), key=lambda i: equity[i])
    returns = [b / a - 1 for a, b in pairwise(equity) if a > 0]
    mean = statistics.fmean(returns) if returns else 0.0
    sd = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    downside = math.sqrt(statistics.fmean([min(r, 0.0) ** 2 for r in returns])) if returns else 0.0
    root = math.sqrt(TRADING_DAYS)
    return {
        "start_equity": _round(first),
        "end_equity": _round(last),
        "total_return_pct": _round(total * 100),
        "cagr_pct": None if cagr is None else _round(cagr * 100),
        "max_drawdown_pct": _round(dd[trough]),
        "max_drawdown_peak": dates[peak].isoformat(),
        "max_drawdown_trough": dates[trough].isoformat(),
        "sharpe": _round(mean / sd * root) if sd > 0 else None,
        "sortino": _round(mean / downside * root) if downside > 0 else None,
        "volatility_pct": _round(sd * root * 100),
    }


def exposure_pct(invested: Sequence[float], equity: Sequence[float]) -> float | None:
    shares = [i / e for i, e in zip(invested, equity, strict=True) if e > 0]
    return _round(statistics.fmean(shares) * 100) if shares else None


def trade_metrics(trades: Sequence[Trade]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "avg_win_pct": None,
            "avg_loss_pct": None,
            "payoff_ratio": None,
            "expectancy_r": None,
            "avg_win_r": None,
            "avg_loss_r": None,
            "profit_factor": None,
            "net_profit": 0.0,
            "avg_sessions": None,
            "best_pct": None,
            "worst_pct": None,
            "stopped_pct": None,
            "partial_pct": None,
        }
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    avg_win = statistics.fmean(t.pnl_pct for t in wins) if wins else None
    avg_loss = statistics.fmean(t.pnl_pct for t in losses) if losses else None
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    stopped = sum(1 for t in trades if (t.exit_reason or "").startswith(("Stop", "Breakeven")))
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": _round(len(wins) / n * 100),
        "avg_win_pct": _round(avg_win),
        "avg_loss_pct": _round(avg_loss),
        "payoff_ratio": _round(avg_win / -avg_loss)
        if avg_win is not None and avg_loss is not None and avg_loss < 0
        else None,
        "expectancy_r": _round(statistics.fmean(t.r_multiple for t in trades)),
        "avg_win_r": _round(statistics.fmean(t.r_multiple for t in wins)) if wins else None,
        "avg_loss_r": _round(statistics.fmean(t.r_multiple for t in losses)) if losses else None,
        "profit_factor": _round(gross_win / gross_loss) if gross_loss > 0 else None,
        "net_profit": _round(gross_win - gross_loss),
        "avg_sessions": _round(statistics.fmean(t.sessions for t in trades), 1),
        "best_pct": _round(max(t.pnl_pct for t in trades)),
        "worst_pct": _round(min(t.pnl_pct for t in trades)),
        "stopped_pct": _round(stopped / n * 100),
        "partial_pct": _round(sum(1 for t in trades if t.partial_done) / n * 100),
    }


def breakdown(trades: Sequence[Trade], key: Callable[[Trade], str | None]) -> list[dict[str, Any]]:
    """Trade metrics per group (market regime at entry, pattern, grade...), largest first."""
    groups: dict[str, list[Trade]] = {}
    for t in trades:
        groups.setdefault(key(t) or "unknown", []).append(t)
    rows = []
    for name, items in groups.items():
        m = trade_metrics(items)
        rows.append(
            {
                "key": name,
                "trades": m["trades"],
                "win_rate_pct": m["win_rate_pct"],
                "expectancy_r": m["expectancy_r"],
                "avg_win_pct": m["avg_win_pct"],
                "avg_loss_pct": m["avg_loss_pct"],
                "profit_factor": m["profit_factor"],
                "net_profit": m["net_profit"],
            }
        )
    return sorted(rows, key=lambda r: (-r["trades"], r["key"]))

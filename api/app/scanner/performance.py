"""Signal performance (spec §8.8, the "honesty page"): how each signal type and score bucket
actually did, from the live signal log and the outcomes tracked after each signal
(app.scanner.outcomes). Pure: the rows come in, the table comes out.

Definitions, at a horizon of N sessions after the signal session:
- measured: signals with at least N sessions observed since (younger ones are still counted
  in "signals" but not in the statistics);
- win rate: the share of measured signals whose close N sessions later is above the signal
  session's close; average gain and loss: the mean of those returns above and at or below 0;
- expectancy in R, for breakouts only (they fire when the entry is reached; other signals fire
  before it): -1R if the low reached the plan's stop within N sessions, else the return from the
  entry to the close N sessions later in units of the plan's risk (entry - stop);
- stop hit: the share of signals with a plan whose low reached the stop within N sessions;
- days to +20%: the median number of sessions until a high 20% above the signal close, among
  the signals that got there (within the 60 sessions tracked); "reached +20%" is their share.
"""

import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.scanner.evaluate import SIGNAL_LABELS

R_TYPES = frozenset({"breakout", "breakout_provisional"})
BUCKETS = ("A+", "A", "B", "C", "Below C")
HORIZONS = (5, 10, 20, 60)


@dataclass(frozen=True)
class SignalRow:
    type: str
    date: date
    grade: str | None
    regime: str | None
    price: float | None
    entry: float | None
    stop: float | None
    returns: dict[int, float | None]  # % from the signal close, by horizon
    observed: int  # sessions observed after the signal session
    stop_hit_after: int | None  # sessions until the stop was reached (1 = the next session)
    gain_20_after: int | None  # sessions until +20%


def bucket(grade: str | None) -> str:
    return grade if grade in BUCKETS else "Below C"


def r_multiple(row: SignalRow, horizon: int) -> float | None:
    """The breakout's result in R at the horizon (see the module docstring)."""
    if row.type not in R_TYPES or row.entry is None or row.stop is None or row.price is None:
        return None
    risk = row.entry - row.stop
    ret = row.returns.get(horizon)
    if risk <= 0:
        return None
    if row.stop_hit_after is not None and row.stop_hit_after <= horizon:
        return -1.0
    if ret is None:
        return None
    return (row.price * (1 + ret / 100) - row.entry) / risk


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def stats(rows: Sequence[SignalRow], horizon: int) -> dict[str, Any]:
    measured = [r for r in rows if r.returns.get(horizon) is not None]
    returns = [float(r.returns[horizon]) for r in measured]  # type: ignore[arg-type]
    gains = [x for x in returns if x > 0]
    losses = [x for x in returns if x <= 0]
    rs = [x for x in (r_multiple(r, horizon) for r in rows) if x is not None]
    with_stop = [
        r
        for r in rows
        if r.stop is not None and (r.observed >= horizon or r.stop_hit_after is not None)
    ]
    stopped = [r for r in with_stop if r.stop_hit_after is not None and r.stop_hit_after <= horizon]
    reached = [r.gain_20_after for r in rows if r.gain_20_after is not None]
    tracked = [r for r in rows if r.observed > 0]
    return {
        "signals": len(rows),
        "measured": len(measured),
        "win_rate_pct": _round(len(gains) / len(measured) * 100, 1) if measured else None,
        "avg_return_pct": _round(statistics.fmean(returns)) if returns else None,
        "avg_gain_pct": _round(statistics.fmean(gains)) if gains else None,
        "avg_loss_pct": _round(statistics.fmean(losses)) if losses else None,
        "expectancy_r": _round(statistics.fmean(rs)) if rs else None,
        "r_count": len(rs),
        "stop_hit_pct": _round(len(stopped) / len(with_stop) * 100, 1) if with_stop else None,
        "reached_20_pct": _round(len(reached) / len(tracked) * 100, 1) if tracked else None,
        "median_days_to_20": statistics.median(reached) if reached else None,
    }


def _grouped(
    rows: Sequence[SignalRow], key: Callable[[SignalRow], str]
) -> dict[str, list[SignalRow]]:
    out: dict[str, list[SignalRow]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def summarize(rows: Sequence[SignalRow], horizon: int) -> dict[str, Any]:
    """Per signal type: all of them, then each grade bucket; and per type × market regime."""
    types = []
    for kind, items in sorted(_grouped(rows, lambda r: r.type).items(), key=lambda kv: -len(kv[1])):
        buckets = _grouped(items, lambda r: bucket(r.grade))
        types.append(
            {
                "type": kind,
                "label": SIGNAL_LABELS.get(kind, kind),
                "r": kind in R_TYPES,
                "all": stats(items, horizon),
                "buckets": [
                    {"bucket": b, **stats(buckets[b], horizon)} for b in BUCKETS if b in buckets
                ],
                "regimes": [
                    {"regime": regime, **stats(group, horizon)}
                    for regime, group in sorted(
                        _grouped(items, lambda r: r.regime or "unknown").items()
                    )
                ],
            }
        )
    return {"horizon": horizon, "total": stats(rows, horizon), "types": types}

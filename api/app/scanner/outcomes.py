"""Signal outcomes (spec §7.1 "Signal outcomes", §8.8): what happened after each signal.

Measured from the signal session's close (the signal's `price`) over the next sessions:
- ret_N: the % change of the close N sessions later (N = 1, 5, 10, 20, 60);
- mfe_pct / mae_pct: the best high and the worst low so far, as % from that close
  (maximum favourable / adverse excursion), over at most 60 sessions;
- stop_hit_on / target_2r_on: the first session whose low reached the plan's stop / whose high
  reached entry + 2 × (entry - stop), when the signal carried a plan;
- gain_20_on: the first session whose high was 20% above the signal close.
A signal's outcome is complete after 60 sessions and never touched again. Market-wide signals
(regime changes) are measured on SPY.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.loaders import read_frame
from app.models import Signal, SignalOutcome, Ticker

log = get_logger(__name__)

HORIZONS = (1, 5, 10, 20, 60)
MAX_HORIZON = HORIZONS[-1]
GAIN_TARGET_PCT = 20.0
MARKET_SYMBOL = "SPY"


@dataclass(frozen=True)
class PathBar:
    date: date
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Outcome:
    sessions_observed: int
    returns: dict[int, float | None]
    mfe_pct: float | None
    mae_pct: float | None
    stop_hit_on: date | None
    target_2r_on: date | None
    gain_20_on: date | None
    complete: bool

    def as_row(self) -> dict[str, Any]:
        return {
            "sessions_observed": self.sessions_observed,
            **{f"ret_{n}": self.returns[n] for n in HORIZONS},
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "stop_hit_on": self.stop_hit_on,
            "target_2r_on": self.target_2r_on,
            "gain_20_on": self.gain_20_on,
            "complete": self.complete,
        }


def _pct(value: float, base: float) -> float:
    return round((value / base - 1) * 100, 2)


def measure(
    base: float,
    after: Sequence[PathBar],
    *,
    entry: float | None = None,
    stop: float | None = None,
) -> Outcome:
    """`after`: the sessions following the signal session, oldest first."""
    path = list(after[:MAX_HORIZON])
    n = len(path)
    returns = {h: (_pct(path[h - 1].close, base) if n >= h else None) for h in HORIZONS}
    target = None
    if entry is not None and stop is not None and entry > stop:
        target = entry + 2 * (entry - stop)
    gain_level = base * (1 + GAIN_TARGET_PCT / 100)

    def first(condition: Any) -> date | None:
        return next((b.date for b in path if condition(b)), None)

    return Outcome(
        sessions_observed=n,
        returns=returns,
        mfe_pct=_pct(max(b.high for b in path), base) if path else None,
        mae_pct=_pct(min(b.low for b in path), base) if path else None,
        stop_hit_on=None if stop is None else first(lambda b: b.low <= stop),
        target_2r_on=None if target is None else first(lambda b: b.high >= target - 1e-9),
        gain_20_on=first(lambda b: b.high >= gain_level - 1e-9),
        complete=n >= MAX_HORIZON,
    )


async def update_outcomes(session: AsyncSession, through: date) -> dict[str, int]:
    """Measure every signal whose outcome isn't complete, with bars up to `through`."""
    rows = (
        await session.execute(
            select(
                Signal.id, Signal.date, Signal.ticker_id, Signal.price, Signal.entry, Signal.stop
            )
            .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
            .where(Signal.date < through, Signal.price.is_not(None))
            .where((SignalOutcome.signal_id.is_(None)) | (SignalOutcome.complete.is_(False)))
        )
    ).all()
    if not rows:
        return {"measured": 0, "completed": 0}
    spy = await session.scalar(
        select(Ticker.id).where(Ticker.symbol == MARKET_SYMBOL, Ticker.is_benchmark)
    )
    wanted: dict[int, date] = {}
    for row in rows:
        key = row.ticker_id if row.ticker_id is not None else spy
        if key is not None:
            wanted[key] = min(row.date, wanted.get(key, row.date))
    paths: dict[int, list[PathBar]] = defaultdict(list)
    ids = list(wanted)
    for first in range(0, len(ids), 500):
        chunk = ids[first : first + 500]
        since = min(wanted[t] for t in chunk)
        frame = await read_frame(
            "SELECT ticker_id, date, high, low, close FROM daily_bars "
            f"WHERE ticker_id IN ({','.join(str(int(t)) for t in chunk)}) "
            f"AND date > '{since.isoformat()}' AND date <= '{through.isoformat()}' "
            "ORDER BY ticker_id, date"
        )
        for tid, day, high, low, close in frame.iter_rows():
            paths[int(tid)].append(PathBar(day, float(high), float(low), float(close)))

    values = []
    completed = 0
    for row in rows:
        key = row.ticker_id if row.ticker_id is not None else spy
        if key is None or row.price is None:
            continue
        after = [b for b in paths.get(key, []) if b.date > row.date]
        outcome = measure(row.price, after, entry=row.entry, stop=row.stop)
        completed += outcome.complete
        values.append({"signal_id": row.id, **outcome.as_row()})
    for first in range(0, len(values), 1000):
        stmt = insert(SignalOutcome).values(values[first : first + 1000])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["signal_id"],
                set_={
                    **{c: stmt.excluded[c] for c in values[0] if c != "signal_id"},
                    "updated_at": text("now()"),
                },
            )
        )
    await session.commit()
    stats = {"measured": len(values), "completed": completed}
    log.info("outcomes.done", through=through.isoformat(), **stats)
    return stats

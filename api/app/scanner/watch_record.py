"""The breakout watch's track record (Phase 8): of the stocks it picked, how many actually broke
out within `watch_hit_sessions` sessions. Pure: picks and breakouts come in as (date, ticker)
pairs, with the trading sessions from the first pick through the latest processed session."""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from statistics import median
from typing import Any


def track_record(
    picks: Sequence[tuple[date, int]],
    breakouts: Sequence[tuple[date, int]],
    sessions: Sequence[date],
    within: int,
) -> dict[str, Any]:
    """A pick is a hit when the stock's first confirmed breakout after the pick comes within
    `within` sessions; pending while fewer sessions have passed; otherwise a miss."""
    by_ticker: dict[int, list[date]] = defaultdict(list)
    for day, ticker in breakouts:
        by_ticker[ticker].append(day)
    for days in by_ticker.values():
        days.sort()
    index = {d: i for i, d in enumerate(sessions)}
    last = len(sessions) - 1
    hits, pending, misses = 0, 0, 0
    waits: list[int] = []
    for day, ticker in picks:
        i = index.get(day)
        if i is None:
            continue
        later = [d for d in by_ticker.get(ticker, []) if d > day]
        first = later[0] if later else None
        k = None if first is None else bisect_left(sessions, first) - i
        if k is not None and 1 <= k <= within:
            hits += 1
            waits.append(k)
        elif last - i < within:
            pending += 1
        else:
            misses += 1
    resolved = hits + misses
    return {
        "picks": hits + pending + misses,
        "hits": hits,
        "misses": misses,
        "pending": pending,
        "within_sessions": within,
        "hit_rate_pct": None if resolved == 0 else round(hits / resolved * 100, 1),
        "median_sessions_to_breakout": None if not waits else float(median(waits)),
        "since": min((d for d, _ in picks), default=None),
    }


__all__ = ["track_record"]

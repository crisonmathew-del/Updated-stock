"""Synthetic charts for the pattern tests.

`chart` draws closes as straight lines between waypoints (session index, price), with a fixed
daily range around each bar and volume levels per segment, then runs the real indicator code
(ATR, SMAs, stage, RS line against a flat benchmark), so detectors see realistic inputs.
`plain_bars` builds a Bars directly from given arrays for hand-checked building-block tests.
"""

from collections.abc import Sequence
from datetime import date

import numpy as np
import polars as pl

from app.core.calendar import sessions_between
from app.indicators.compute import compute_indicators
from app.patterns.bars import Bars, rolling_mean

START = date(2019, 1, 2)
BASE_VOLUME = 1_000_000.0


def sessions(n: int) -> list[date]:
    days = sessions_between(START, date(2026, 12, 31))
    return days[:n]


def chart(
    waypoints: Sequence[tuple[int, float]],
    *,
    range_pct: float = 1.0,
    volumes: Sequence[tuple[int, float]] = ((0, 1.0),),
    volume_on: dict[int, float] | None = None,
    open_on: dict[int, float] | None = None,
    corrections: set[date] | None = None,
    cut: int | None = None,
    scale: float = 1.0,
) -> Bars:
    """`volumes`: (from session, multiple of 1M shares) levels; `volume_on` / `open_on`
    override single sessions (volume multiple / opening price). `cut` keeps only sessions
    0..cut *before* computing indicators (what the system knew on that day); `scale`
    multiplies every price."""
    n = waypoints[-1][0] + 1
    xs, ys = zip(*waypoints, strict=True)
    close = np.interp(np.arange(n), xs, ys)
    open_ = np.concatenate([[close[0]], close[:-1]])
    for i, price in (open_on or {}).items():
        open_[i] = price
    half = range_pct / 200
    high = np.maximum(open_, close) * (1 + half)
    low = np.minimum(open_, close) * (1 - half)
    level = np.ones(n)
    for start, multiple in volumes:
        level[start:] = multiple
    for i, multiple in (volume_on or {}).items():
        level[i] = multiple
    days = sessions(n)
    open_, high, low, close = (a * scale for a in (open_, high, low, close))
    frame = pl.DataFrame(
        {
            "ticker_id": [1] * n,
            "date": days,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": (level * BASE_VOLUME).round().astype(np.int64),
        }
    )
    if cut is not None:
        frame = frame.head(cut + 1)
        days = days[: cut + 1]
    benchmark = pl.DataFrame({"date": days, "close": [100.0] * len(days)})
    indicators = compute_indicators(frame, benchmark)
    joined = indicators.join(frame.select("date", "open"), on="date")
    return Bars.from_frame(joined, corrections)


def plain_bars(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float] | None = None,
    *,
    volume: Sequence[float] | None = None,
    stage: Sequence[float] | float = 2,
    avg_volume: float = float("nan"),
) -> Bars:
    n = len(high)
    closes = np.array(
        close if close is not None else [(h + lo) / 2 for h, lo in zip(high, low, strict=True)],
        dtype=float,
    )
    stages = (
        np.full(n, float(stage)) if isinstance(stage, int | float) else np.array(stage, dtype=float)
    )
    nan = np.full(n, np.nan)
    return Bars(
        dates=sessions(n),
        open=closes.copy(),
        high=np.array(high, dtype=float),
        low=np.array(low, dtype=float),
        close=closes,
        volume=np.array(volume if volume is not None else [BASE_VOLUME] * n, dtype=float),
        atr14=nan.copy(),
        sma10=rolling_mean(closes, 10),
        sma50=nan.copy(),
        avg_volume_50=np.full(n, avg_volume),
        atr_ratio_10_50=nan.copy(),
        bb_width=nan.copy(),
        rs_line=nan.copy(),
        stage=stages,
        market_correction=np.zeros(n, dtype=np.bool_),
    )

"""One ticker's daily bars and indicators as NumPy arrays, plus weekly bars.

Detectors index these arrays directly (the swing and candidate loops are sequential), which is
much faster than per-row Polars access. `Bars.until` cuts everything at the as-of session.
"""

from dataclasses import dataclass, fields, replace
from datetime import date
from functools import cached_property

import numpy as np
import numpy.typing as npt
import polars as pl

Floats = npt.NDArray[np.float64]

# Indicator columns the detectors read (computed by app.indicators; null → NaN).
INDICATORS = (
    "ema10",
    "ema21",
    "atr14",
    "sma50",
    "avg_volume_50",
    "atr_ratio_10_50",
    "bb_width",
    "rs_line",
    "stage",
)


@dataclass(frozen=True)
class Bars:
    dates: list[date]
    open: Floats
    high: Floats
    low: Floats
    close: Floats
    volume: Floats
    atr14: Floats
    ema10: Floats
    ema21: Floats
    sma10: Floats
    sma50: Floats
    avg_volume_50: Floats
    atr_ratio_10_50: Floats
    bb_width: Floats
    rs_line: Floats
    stage: Floats
    # True on sessions when the overall market regime was "correction" (cups may be deeper).
    market_correction: npt.NDArray[np.bool_]

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def last(self) -> int:
        return len(self.dates) - 1

    @cached_property
    def days(self) -> npt.NDArray[np.datetime64]:
        """`dates` as a datetime64[D] array (for searching)."""
        return np.array(self.dates, dtype="datetime64[D]")

    def until(self, as_of: date) -> "Bars":
        """Every array cut after the last session on or before `as_of`."""
        return self.slice(0, int(np.searchsorted(self.days, np.datetime64(as_of), side="right")))

    def window(self, start: date, end: date) -> "Bars":
        """The sessions from `start` through `end` (both inclusive)."""
        lo = int(np.searchsorted(self.days, np.datetime64(start), side="left"))
        hi = int(np.searchsorted(self.days, np.datetime64(end), side="right"))
        return self.slice(lo, hi)

    def slice(self, lo: int, hi: int) -> "Bars":
        """Sessions `lo` up to (not including) `hi`: views of the arrays, no copies."""
        if lo == 0 and hi >= len(self.dates):
            return self
        out = replace(self, **{f.name: getattr(self, f.name)[lo:hi] for f in fields(self)})
        out.__dict__["days"] = self.days[lo:hi]  # a view: windows of one history share it
        return out

    @classmethod
    def from_frame(cls, frame: pl.DataFrame, correction_dates: set[date] | None = None) -> "Bars":
        """From one ticker's rows sorted by date with open/high/low/close/volume and the
        INDICATORS columns (missing indicator columns are treated as unknown)."""
        frame = frame.sort("date")

        def col(name: str) -> Floats:
            if name not in frame.columns:
                return np.full(frame.height, np.nan)
            return frame[name].cast(pl.Float64).fill_null(np.nan).to_numpy().astype(np.float64)

        close = col("close")
        dates = frame["date"].to_list()
        corrections = correction_dates or set()
        return cls(
            dates=dates,
            open=col("open"),
            high=col("high"),
            low=col("low"),
            close=close,
            volume=col("volume"),
            atr14=col("atr14"),
            ema10=col("ema10"),
            ema21=col("ema21"),
            sma10=rolling_mean(close, 10),
            sma50=col("sma50"),
            avg_volume_50=col("avg_volume_50"),
            atr_ratio_10_50=col("atr_ratio_10_50"),
            bb_width=col("bb_width"),
            rs_line=col("rs_line"),
            stage=col("stage"),
            market_correction=np.array([d in corrections for d in dates], dtype=np.bool_),
        )


def rolling_mean(values: Floats, n: int) -> Floats:
    """Trailing mean over `n` values (NaN until `n` are available)."""
    out = np.full(len(values), np.nan)
    if len(values) >= n:
        cumulative = np.cumsum(np.insert(values, 0, 0.0))
        out[n - 1 :] = (cumulative[n:] - cumulative[:-n]) / n
    return out


@dataclass(frozen=True)
class WeeklyBars:
    """Weekly bars built from daily ones (a week = an ISO calendar week). `first`/`last` are
    the daily indexes each week spans; `complete` is False for a week still in progress."""

    dates: list[date]  # the week's last session so far
    open: Floats
    high: Floats
    low: Floats
    close: Floats
    volume: Floats
    atr14: Floats
    first: npt.NDArray[np.int64]
    last: npt.NDArray[np.int64]
    complete: npt.NDArray[np.bool_]

    def __len__(self) -> int:
        return len(self.dates)


def weekly(bars: Bars, last_week_complete: bool) -> WeeklyBars:
    """Aggregate to weeks. The final week counts as complete only if `last_week_complete`
    (the as-of session is the last session of its week, known from the exchange calendar)."""
    if len(bars) == 0:
        empty = np.array([], dtype=np.float64)
        ints = np.array([], dtype=np.int64)
        return WeeklyBars(
            [], empty, empty, empty, empty, empty, empty, ints, ints, np.array([], dtype=np.bool_)
        )
    # Monday-based week number (1970-01-01 was a Thursday): equal within an ISO week.
    week = (bars.days.astype(np.int64) + 3) // 7
    first = np.flatnonzero(np.concatenate(([True], week[1:] != week[:-1]))).astype(np.int64)
    last = np.concatenate((first[1:] - 1, [len(week) - 1])).astype(np.int64)
    high = np.maximum.reduceat(bars.high, first)
    low = np.minimum.reduceat(bars.low, first)
    close = bars.close[last]
    complete = np.ones(len(first), dtype=np.bool_)
    complete[-1] = last_week_complete
    return WeeklyBars(
        dates=[bars.dates[i] for i in last],
        open=bars.open[first],
        high=high,
        low=low,
        close=close,
        volume=np.add.reduceat(bars.volume, first),
        atr14=wilder_atr(high, low, close, 14),
        first=first,
        last=last,
        complete=complete,
    )


def wilder_atr(high: Floats, low: Floats, close: Floats, n: int) -> Floats:
    """Wilder's ATR over a few hundred bars, identical to app.indicators.atr.add_atr (same
    seed and the same `v + alpha * (x - v)` step, so the same bits) without a Polars round trip."""
    out = np.full(len(high), np.nan)
    if len(high) < n:
        return out
    prev = np.concatenate(([np.nan], close[:-1]))
    true_range = np.fmax(high - low, np.fmax(np.abs(high - prev), np.abs(low - prev)))
    value = float(np.sum(true_range[:n])) / n
    out[n - 1] = value
    alpha = 1 / n
    for i, x in enumerate(true_range[n:].tolist(), start=n):
        value = value + alpha * (x - value)
        out[i] = value
    return out

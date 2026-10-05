"""One ticker's daily bars and indicators as NumPy arrays, plus weekly bars.

Detectors index these arrays directly (the swing and candidate loops are sequential), which is
much faster than per-row Polars access. `Bars.until` cuts everything at the as-of session.
"""

from dataclasses import dataclass, fields, replace
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl

from app.indicators.atr import add_atr

Floats = npt.NDArray[np.float64]

# Indicator columns the detectors read (computed by app.indicators; null → NaN).
INDICATORS = (
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

    def until(self, as_of: date) -> "Bars":
        """Every array cut after the last session on or before `as_of`."""
        n = int(
            np.searchsorted(
                np.array(self.dates, dtype="datetime64[D]"), np.datetime64(as_of), side="right"
            )
        )
        values = {}
        for f in fields(self):
            value = getattr(self, f.name)
            values[f.name] = value[:n]
        return replace(self, **values)

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
    keys = [d.isocalendar()[:2] for d in bars.dates]
    starts = [0] + [i for i in range(1, len(keys)) if keys[i] != keys[i - 1]]
    ends = [*(s - 1 for s in starts[1:]), len(keys) - 1]
    first = np.array(starts, dtype=np.int64)
    last = np.array(ends, dtype=np.int64)
    high = np.array([bars.high[a : b + 1].max() for a, b in zip(starts, ends, strict=True)])
    low = np.array([bars.low[a : b + 1].min() for a, b in zip(starts, ends, strict=True)])
    volume = np.array([bars.volume[a : b + 1].sum() for a, b in zip(starts, ends, strict=True)])
    complete = np.ones(len(starts), dtype=np.bool_)
    complete[-1] = last_week_complete
    frame = pl.DataFrame({"ticker_id": 0, "high": high, "low": low, "close": bars.close[last]})
    atr = add_atr(frame, 14)["atr14"].fill_null(np.nan).to_numpy().astype(np.float64)
    return WeeklyBars(
        dates=[bars.dates[i] for i in ends],
        open=bars.open[first],
        high=high,
        low=low,
        close=bars.close[last],
        volume=volume,
        atr14=atr,
        first=first,
        last=last,
        complete=complete,
    )

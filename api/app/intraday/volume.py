"""Time-of-day volume projection (spec §6.8): how much volume a stock will trade by the close,
from what it has traded so far and the share of a normal day's volume traded by this minute.

`VolumeCurve.fraction(m)` is the cumulative share of a whole day's volume (closing auction
included) traded `m` minutes after the open, on a 390-minute scale. Until enough minute bars
are stored to learn the curve (`learn_curve`), the standard curve below is used: a typical US
large-cap profile, heavy at the open, light at midday, heavy into the close, with about 8% of
the day left for the closing auction (which prints at 16:00, after the last regular minute).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import polars as pl

from app.intraday.session import REGULAR_MINUTES, minutes_elapsed

# (minutes after the open, cumulative share of the day's volume). Linear in between.
STANDARD_ANCHORS: tuple[tuple[int, float], ...] = (
    (0, 0.0),
    (5, 0.030),
    (15, 0.075),
    (30, 0.133),
    (60, 0.227),
    (90, 0.305),
    (120, 0.372),
    (150, 0.427),
    (180, 0.477),
    (210, 0.524),
    (240, 0.570),
    (270, 0.620),
    (300, 0.676),
    (330, 0.737),
    (360, 0.809),
    (390, 0.920),
)
AUCTION_SHARE = 1 - STANDARD_ANCHORS[-1][1]  # traded in the closing auction


@dataclass(frozen=True)
class VolumeCurve:
    """391 cumulative fractions, one per minute boundary from the open (0) to the close."""

    cumulative: tuple[float, ...]
    source: str  # "standard" or "learned from N sessions"

    @classmethod
    def from_anchors(cls, anchors: Sequence[tuple[int, float]], source: str) -> "VolumeCurve":
        xs = [a[0] for a in anchors]
        ys = [a[1] for a in anchors]
        points = np.interp(np.arange(REGULAR_MINUTES + 1), xs, ys)
        return cls(tuple(float(v) for v in points), source)

    def fraction(self, minutes: float) -> float:
        """Cumulative share at `minutes` after the open (linear between whole minutes)."""
        m = min(float(REGULAR_MINUTES), max(0.0, minutes))
        low = int(m)
        if low >= REGULAR_MINUTES:
            return self.cumulative[REGULAR_MINUTES]
        weight = m - low
        return self.cumulative[low] * (1 - weight) + self.cumulative[low + 1] * weight

    def project(self, volume_so_far: float, ts: datetime) -> float | None:
        """The day's projected volume, given the regular-session volume traded by `ts`
        (None before any volume is expected)."""
        share = self.fraction(minutes_elapsed(ts))
        if share <= 0:
            return None
        return volume_so_far / share


STANDARD_CURVE = VolumeCurve.from_anchors(STANDARD_ANCHORS, "standard")


def learn_curve(minute_bars: pl.DataFrame, min_sessions: int) -> VolumeCurve | None:
    """The median time-of-day curve from stored minute bars (columns `ticker_id`, `session`,
    `minute` (0-389 after the open), `volume`), or None with fewer than `min_sessions` distinct
    sessions. Each (stock, session) with bars for at least 95% of the minutes counts once; its
    curve is scaled so the last regular minute reaches the standard pre-auction share."""
    if minute_bars.is_empty():
        return None
    complete = (
        minute_bars.group_by("ticker_id", "session")
        .agg(pl.len().alias("n"), pl.col("volume").sum().alias("total"))
        .filter((pl.col("n") >= 0.95 * REGULAR_MINUTES) & (pl.col("total") > 0))
    )
    sessions = complete["session"].n_unique() if complete.height else 0
    if sessions < min_sessions:
        return None
    rows = minute_bars.join(
        complete.select("ticker_id", "session", "total"), on=["ticker_id", "session"]
    )
    grid = (
        rows.sort("ticker_id", "session", "minute")
        .with_columns(
            (pl.col("volume").cum_sum().over("ticker_id", "session") / pl.col("total")).alias(
                "share"
            )
        )
        # Cumulative share at the *end* of each minute = the curve at minute + 1.
        .group_by("minute")
        .agg(pl.col("share").median())
        .sort("minute")
    )
    shares = dict(zip(grid["minute"].to_list(), grid["share"].to_list(), strict=True))
    points = [0.0]
    for minute in range(REGULAR_MINUTES):
        points.append(max(points[-1], float(shares.get(minute, points[-1]))))
    scale = (1 - AUCTION_SHARE) / points[-1] if points[-1] > 0 else 0
    return VolumeCurve(tuple(p * scale for p in points), f"learned from {sessions} sessions")

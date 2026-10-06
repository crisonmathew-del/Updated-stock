"""The scans that run on the live feed's clock (spec §7.1), over the scan universe (active
setups, followed stocks and Stage 2 leaders), from feed snapshots. Pure.

- **Pre-market** (08:00-09:25 US/Eastern, every 5 minutes): a gap of at least
  `premarket_gap_min_pct` from the previous close, with pre-market volume of at least
  `premarket_volume_min_pct_of_avg` of the 50-day average. A stock that reported after
  yesterday's close or before today's open is flagged as an earnings reaction.
- **Intraday sweep** (regular session, every 15 minutes): projected volume at least
  `sweep_volume_ratio_min` × the 50-day average and a move of at least `sweep_min_change_pct`
  on the day (either way).

A partial (IEX) feed's volumes are scaled up by its share of the market, as for the watcher.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.intraday.session import minutes_elapsed
from app.intraday.volume import VolumeCurve
from app.providers.base import Snapshot
from app.settings.schema import AppSettings


@dataclass(frozen=True)
class Candidate:
    symbol: str
    ticker_id: int
    name: str
    prev_close: float | None
    avg_volume: float | None
    grade: str | None = None  # the active setup's grade
    setup_state: str | None = None
    earnings: bool = False  # reported after the previous close or before today's open


@dataclass(frozen=True)
class ScanHit:
    scan: str  # premarket | sweep
    symbol: str
    ticker_id: int
    name: str
    at: datetime
    price: float
    prev_close: float
    change_pct: float
    volume: float  # pre-market volume (pre-market scan) or projected volume (sweep), scaled
    volume_pct: float  # of the 50-day average
    earnings: bool
    grade: str | None
    setup_state: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "scan": self.scan,
            "symbol": self.symbol,
            "name": self.name,
            "at": self.at.isoformat(),
            "price": self.price,
            "prev_close": self.prev_close,
            "change_pct": round(self.change_pct, 2),
            "volume": round(self.volume),
            "volume_pct": round(self.volume_pct, 1),
            "earnings": self.earnings,
            "grade": self.grade,
            "setup_state": self.setup_state,
        }

    def title(self) -> str:
        if self.scan == "premarket":
            way = "up" if self.change_pct > 0 else "down"
            reason = " on earnings" if self.earnings else ""
            return f"{self.symbol} gapping {way} {abs(self.change_pct):.1f}% pre-market{reason}"
        return f"{self.symbol} {'up' if self.change_pct > 0 else 'down'} on heavy volume"

    def body(self) -> str:
        move = f"{self.price:,.2f} vs the {self.prev_close:,.2f} close ({self.change_pct:+.1f}%)"
        if self.scan == "premarket":
            note = " An earnings reaction." if self.earnings else ""
            return (
                f"{move}; pre-market volume {self.volume:,.0f}, {self.volume_pct:.0f}% of the "
                f"50-day average.{note}"
            )
        return f"{move} on projected volume {self.volume_pct / 100:.1f}× the 50-day average."


def premarket_hits(
    candidates: dict[str, Candidate],
    snapshots: dict[str, Snapshot],
    settings: AppSettings,
    now: datetime,
    volume_share: float = 1.0,
) -> list[ScanHit]:
    hits = []
    for symbol, snap in snapshots.items():
        c = candidates.get(symbol)
        prev = (c.prev_close if c else None) or snap.prev_close
        if c is None or snap.last is None or not prev or not c.avg_volume:
            continue
        gap = (snap.last / prev - 1) * 100
        volume = snap.premarket_volume / volume_share
        volume_pct = volume / c.avg_volume * 100
        if abs(gap) < settings.premarket_gap_min_pct:
            continue
        if volume_pct < settings.premarket_volume_min_pct_of_avg:
            continue
        hits.append(
            ScanHit(
                "premarket",
                symbol,
                c.ticker_id,
                c.name,
                now,
                snap.last,
                prev,
                gap,
                volume,
                volume_pct,
                c.earnings,
                c.grade,
                c.setup_state,
            )
        )
    return sorted(hits, key=lambda h: -abs(h.change_pct))


def sweep_hits(
    candidates: dict[str, Candidate],
    snapshots: dict[str, Snapshot],
    settings: AppSettings,
    curve: VolumeCurve,
    now: datetime,
    volume_share: float = 1.0,
) -> list[ScanHit]:
    if minutes_elapsed(now) < settings.intraday_projection_min_minutes:
        return []
    hits = []
    for symbol, snap in snapshots.items():
        c = candidates.get(symbol)
        prev = (c.prev_close if c else None) or snap.prev_close
        if c is None or snap.last is None or not prev or not c.avg_volume:
            continue
        projected = curve.project(snap.volume / volume_share, now)
        if projected is None:
            continue
        change = (snap.last / prev - 1) * 100
        ratio = projected / c.avg_volume
        if ratio < settings.sweep_volume_ratio_min or abs(change) < settings.sweep_min_change_pct:
            continue
        hits.append(
            ScanHit(
                "sweep",
                symbol,
                c.ticker_id,
                c.name,
                now,
                snap.last,
                prev,
                change,
                projected,
                ratio * 100,
                c.earnings,
                c.grade,
                c.setup_state,
            )
        )
    return sorted(hits, key=lambda h: -h.volume_pct)

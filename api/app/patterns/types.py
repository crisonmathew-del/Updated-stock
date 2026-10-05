"""What a detector returns: enough to explain the score and draw the pattern."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class PatternType(StrEnum):
    VCP = "vcp"
    CUP_WITH_HANDLE = "cup_with_handle"
    FLAT_BASE = "flat_base"
    HIGH_TIGHT_FLAG = "high_tight_flag"
    THREE_WEEKS_TIGHT = "three_weeks_tight"
    ASCENDING_BASE = "ascending_base"
    POCKET_PIVOT = "pocket_pivot"
    EARNINGS_GAP = "earnings_gap"


LABELS: dict[PatternType, str] = {
    PatternType.VCP: "Volatility contraction (VCP)",
    PatternType.CUP_WITH_HANDLE: "Cup with handle",
    PatternType.FLAT_BASE: "Flat base",
    PatternType.HIGH_TIGHT_FLAG: "High tight flag",
    PatternType.THREE_WEEKS_TIGHT: "Three weeks tight",
    PatternType.ASCENDING_BASE: "Ascending base",
    PatternType.POCKET_PIVOT: "Pocket pivot",
    PatternType.EARNINGS_GAP: "Earnings gap",
}
EVENTS = frozenset({PatternType.POCKET_PIVOT, PatternType.EARNINGS_GAP})


class Status(StrEnum):
    FORMING = "forming"  # still inside the base (events: still valid)
    BROKEN_OUT = "broken_out"  # closed above the pivot in the last few sessions
    FAILED = "failed"  # closed below the base low (events: below the event-day low)
    EXPIRED = "expired"  # no longer matches the rules (set by the pipeline)


@dataclass(frozen=True, slots=True)
class Point:
    """A swing point or marker for the chart."""

    date: date
    price: float
    kind: str  # high | low

    def as_dict(self) -> dict[str, Any]:
        return {"date": self.date.isoformat(), "price": round(self.price, 4), "kind": self.kind}


@dataclass(frozen=True, slots=True)
class Contraction:
    high: Point
    low: Point
    depth_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "high": self.high.as_dict(),
            "low": self.low.as_dict(),
            "depth_pct": round(self.depth_pct, 2),
        }


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    key: str
    label: str
    points: float
    max_points: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "points": round(self.points, 2),
            "max_points": self.max_points,
            "detail": self.detail,
        }


@dataclass
class PatternMatch:
    type: PatternType
    timeframe: str  # daily | weekly
    start: date
    end: date
    pivot: float
    status: Status
    duration_weeks: float
    components: list[ScoreComponent]
    base_low: float | None = None
    depth_pct: float | None = None
    base_number: int | None = None
    swings: list[Point] = field(default_factory=list)
    contractions: list[Contraction] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def quality(self) -> float:
        return round(sum(c.points for c in self.components), 2)

    def as_row(self) -> dict[str, Any]:
        """Column values for the `patterns` table (ids and bookkeeping added by the caller)."""
        return {
            "type": str(self.type),
            "timeframe": self.timeframe,
            "start_date": self.start,
            "end_date": self.end,
            "pivot": round(self.pivot, 4),
            "base_low": None if self.base_low is None else round(self.base_low, 4),
            "depth_pct": None if self.depth_pct is None else round(self.depth_pct, 2),
            "duration_weeks": round(self.duration_weeks, 1),
            "quality": self.quality,
            "base_number": self.base_number,
            "status": str(self.status),
            "components": [c.as_dict() for c in self.components],
            "swings": [p.as_dict() for p in self.swings],
            "contractions": [c.as_dict() for c in self.contractions],
            "details": self.details,
        }


class Scorecard:
    """Collects named components; each earns `fraction` (clamped to 0-1) of its points."""

    def __init__(self) -> None:
        self.components: list[ScoreComponent] = []

    def add(self, key: str, label: str, max_points: float, fraction: float, detail: str) -> None:
        share = min(1.0, max(0.0, fraction))
        self.components.append(ScoreComponent(key, label, max_points * share, max_points, detail))

"""Setup Score 0-100 (spec §6.10): a sum of named components, then
`final = raw × regime multiplier - red-flag penalties`.

Components (points from settings.setup_weights):
- trend: Trend Template checks passed (share of 80% of the points) plus Stage 2 (20%).
- relative_strength: RS Rating, scaled from 50 (nothing) to 99 (70% of the points), plus the
  RS line (30%): at a 52-week high earns it all, merely rising over 3 months half.
- fundamentals: the Fundamentals Grade (A 100%, B 80%, C 60%, D 30%, E 0%).
- pattern: the base's quality score (0-100) as a share of the points; no base scores 0.
- group: industry group rank: top 20 full, top 40 70%, falling to 0 at the last group.
- accumulation: 50-day up/down volume (50%: full at the accumulation threshold, half at
  1.0), a pocket pivot in the last 10 sessions (30%), an insider cluster buy (20%).
  Institutional sponsorship (13F) arrives with a paid provider.

A component without data (no grade, no RS Rating yet, no industry group) is left out and the
others are scaled up to 100; `coverage_pct` and the component's detail say so. Grades:
A+/A/B/C from settings.setup_grade_cutoffs; below C is not a recommendation (grade None).
"""

from dataclasses import dataclass, field
from typing import Any

from app.scoring.red_flags import RedFlag
from app.settings.schema import AppSettings

GRADE_SHARE = {"A": 1.0, "B": 0.8, "C": 0.6, "D": 0.3, "E": 0.0}
REGIME_KEYS = {
    "confirmed_uptrend": "confirmed_uptrend",
    "uptrend_under_pressure": "uptrend_under_pressure",
    "correction": "correction",
}


@dataclass(frozen=True)
class ScoreInputs:
    tt_passed: int | None = None  # Trend Template checks passed, out of 8
    stage: int | None = None
    rs_rating: int | None = None
    rs_line_high_52w: bool = False
    rs_new_high_ahead: bool = False
    rs_slope_63: float | None = None
    fundamentals_grade: str | None = None
    pattern_quality: float | None = None
    pattern_label: str | None = None
    group_name: str | None = None
    group_rank: int | None = None
    groups_ranked: int | None = None
    up_down_volume: float | None = None
    pocket_pivots_recent: int = 0
    insider_cluster: bool = False
    regime: str | None = None  # market state key
    regime_label: str | None = None


@dataclass
class Component:
    key: str
    label: str
    max_points: float
    points: float
    status: str  # pass | partial | fail | no_data
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "points": round(self.points, 2),
            "max_points": self.max_points,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class SetupScore:
    raw: float
    multiplier: float
    penalties: float
    final: float
    grade: str | None
    coverage_pct: float
    components: list[Component] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    regime_note: str = ""

    def components_json(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.components]

    def red_flags_json(self) -> list[dict[str, Any]]:
        return [f.as_dict() for f in self.red_flags]


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def _status(points: float, max_points: float) -> str:
    if points >= max_points - 1e-9:
        return "pass"
    return "partial" if points > 0 else "fail"


def _scale(value: float, zero_at: float, full_at: float) -> float:
    return min(1.0, max(0.0, (value - zero_at) / (full_at - zero_at)))


def letter(final: float, settings: AppSettings) -> str | None:
    c = settings.setup_grade_cutoffs
    for name, cutoff in (("A+", c.a_plus), ("A", c.a), ("B", c.b), ("C", c.c)):
        if final >= cutoff:
            return name
    return None


def readiness_pct(close: float, pivot: float | None) -> float | None:
    """How far the close is below the pivot, in % of the close (negative: above it)."""
    if pivot is None or close <= 0:
        return None
    return round((pivot - close) / close * 100, 2)


def score_setup(
    inputs: ScoreInputs, settings: AppSettings, red_flags: list[RedFlag] | None = None
) -> SetupScore:
    w = settings.setup_weights
    i = inputs
    parts: list[Component] = []

    if i.tt_passed is None:
        parts.append(
            Component(
                "trend",
                "Trend Template & stage",
                w.trend,
                0,
                "no_data",
                "No Trend Template yet (needs 200+ sessions).",
            )
        )
    else:
        stage2 = i.stage == 2
        points = w.trend * (0.8 * i.tt_passed / 8 + 0.2 * stage2)
        stage = f"Stage {i.stage}" if i.stage else "stage unknown"
        parts.append(
            Component(
                "trend",
                "Trend Template & stage",
                w.trend,
                points,
                _status(points, w.trend),
                f"{i.tt_passed}/8 Trend Template checks pass; {stage}.",
            )
        )

    if i.rs_rating is None:
        parts.append(
            Component(
                "relative_strength",
                "Relative strength",
                w.relative_strength,
                0,
                "no_data",
                "No RS Rating yet.",
            )
        )
    else:
        rating = 0.7 * _scale(i.rs_rating, 50, 99)
        if i.rs_line_high_52w:
            line, line_text = (
                0.3,
                "RS line at a 52-week high" + (" ahead of price" if i.rs_new_high_ahead else ""),
            )
        elif i.rs_slope_63 is not None and i.rs_slope_63 > 0:
            line, line_text = 0.15, f"RS line up {i.rs_slope_63 * 100:.1f}% over 3 months"
        else:
            line, line_text = 0.0, "RS line not rising"
        points = w.relative_strength * (rating + line)
        parts.append(
            Component(
                "relative_strength",
                "Relative strength",
                w.relative_strength,
                points,
                _status(points, w.relative_strength),
                f"RS Rating {i.rs_rating}; {line_text}.",
            )
        )

    if i.fundamentals_grade is None:
        parts.append(
            Component(
                "fundamentals",
                "Fundamentals Grade",
                w.fundamentals,
                0,
                "no_data",
                "Fundamentals unknown (no grade): left out, the other parts scaled up.",
            )
        )
    else:
        points = w.fundamentals * GRADE_SHARE.get(i.fundamentals_grade, 0.0)
        parts.append(
            Component(
                "fundamentals",
                "Fundamentals Grade",
                w.fundamentals,
                points,
                _status(points, w.fundamentals),
                f"Grade {i.fundamentals_grade}.",
            )
        )

    if i.pattern_quality is None:
        parts.append(Component("pattern", "Pattern quality", w.pattern, 0, "fail", "No base yet."))
    else:
        points = w.pattern * min(100.0, max(0.0, i.pattern_quality)) / 100
        parts.append(
            Component(
                "pattern",
                "Pattern quality",
                w.pattern,
                points,
                _status(points, w.pattern),
                f"{i.pattern_label or 'Base'} quality {i.pattern_quality:.0f}/100.",
            )
        )

    if i.group_rank is None or not i.groups_ranked:
        parts.append(
            Component(
                "group", "Industry group rank", w.group, 0, "no_data", "No industry group rank."
            )
        )
    else:
        n = i.groups_ranked
        if i.group_rank <= 20:
            share = 1.0
        elif i.group_rank <= 40:
            share = 0.7
        else:
            share = 0.7 * max(0.0, (n - i.group_rank) / max(1, n - 40))
        points = w.group * share
        parts.append(
            Component(
                "group",
                "Industry group rank",
                w.group,
                points,
                _status(points, w.group),
                f"{i.group_name or 'Group'} ranks {i.group_rank} of {n}.",
            )
        )

    minimum = settings.accumulation_up_down_ratio_min
    ratio = i.up_down_volume
    ratio_share = (
        0.0 if ratio is None else 0.5 if ratio >= minimum else 0.25 if ratio >= 1.0 else 0.0
    )
    share = ratio_share + 0.3 * (i.pocket_pivots_recent > 0) + 0.2 * i.insider_cluster
    pieces = [
        "up/down volume n/a"
        if ratio is None
        else f"up/down volume {ratio:.2f} (accumulation at {minimum:g})",
        f"{i.pocket_pivots_recent} pocket pivot(s) in the last 10 sessions",
        "insider cluster buy" if i.insider_cluster else "no insider cluster buy",
    ]
    points = w.accumulation * share
    parts.append(
        Component(
            "accumulation",
            "Accumulation",
            w.accumulation,
            points,
            _status(points, w.accumulation),
            _sentence("; ".join(pieces)),
        )
    )

    with_data = [c for c in parts if c.status != "no_data"]
    available = sum(c.max_points for c in with_data)
    total = sum(c.max_points for c in parts)
    raw = 100 * sum(c.points for c in with_data) / available if available else 0.0
    multipliers = settings.regime_multipliers
    if i.regime in REGIME_KEYS:
        multiplier = float(getattr(multipliers, REGIME_KEYS[i.regime]))
        note = f"{i.regime_label or i.regime}: × {multiplier:g}"
    else:
        multiplier, note = 1.0, "Market regime unknown: × 1"
    flags = list(red_flags or [])
    penalties = sum(f.penalty for f in flags)
    final = max(0.0, raw * multiplier - penalties)
    return SetupScore(
        raw=round(raw, 2),
        multiplier=multiplier,
        penalties=round(penalties, 2),
        final=round(final, 2),
        grade=letter(round(final, 2), settings),
        coverage_pct=round(100 * available / total, 1) if total else 0.0,
        components=parts,
        red_flags=flags,
        regime_note=note,
    )

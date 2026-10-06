"""Run every detector on one ticker as of its last bar."""

from collections.abc import Sequence
from datetime import date

from app.patterns.bars import Bars, weekly
from app.patterns.bases import (
    detect_ascending_base,
    detect_cup_with_handle,
    detect_flat_base,
    detect_high_tight_flag,
    detect_vcp,
)
from app.patterns.events import detect_earnings_gaps, detect_pocket_pivots
from app.patterns.swings import atr_threshold, pct_threshold, with_pending, zigzag
from app.patterns.types import PatternMatch, Status
from app.patterns.weekly import detect_three_weeks_tight
from app.settings.schema import AppSettings

# Fewer sessions than this can't hold a base plus the advance before it.
MIN_SESSIONS = 60


def detect_patterns(
    bars: Bars,
    settings: AppSettings,
    *,
    last_week_complete: bool,
    releases: Sequence[tuple[date, str]] = (),
) -> list[PatternMatch]:
    """Every base forming (or just broken out) and every recent event, as of the last bar.
    `releases` are (date, timing) of results releases known by then; `last_week_complete`
    says whether the last bar is the final session of its week."""
    [matches] = detect_variants(
        bars, [settings], last_week_complete=last_week_complete, releases=releases
    )
    return matches


def detect_variants(
    bars: Bars,
    variants: Sequence[AppSettings],
    *,
    last_week_complete: bool,
    releases: Sequence[tuple[date, str]] = (),
) -> list[list[PatternMatch]]:
    """`detect_patterns` for settings that differ only in their `vcp_*` thresholds (the
    backtest's sensitivity grid), one list per variant, each equal to a separate call: the
    swings, weekly bars and the other detectors run once (with the first variant)."""
    if len(bars) < MIN_SESSIONS:
        return [[] for _ in variants]
    settings = variants[0]
    multiple = settings.swing_atr_multiple
    daily = with_pending(*zigzag(bars.high, bars.low, atr_threshold(bars.atr14, multiple)))
    weeks = weekly(bars, last_week_complete)
    weekly_swings, _ = zigzag(weeks.high, weeks.low, atr_threshold(weeks.atr14, multiple))
    pullbacks = with_pending(
        *zigzag(bars.high, bars.low, pct_threshold(bars.close, settings.ascending_pullback_min_pct))
    )
    others = [
        detect_flat_base(bars, settings, daily),
        detect_cup_with_handle(bars, settings, daily, weeks, weekly_swings),
        detect_ascending_base(bars, settings, pullbacks),
        detect_high_tight_flag(bars, settings, daily),
        detect_three_weeks_tight(bars, settings, weeks),
    ]
    gaps = detect_earnings_gaps(bars, settings, releases)
    pivots: dict[bool, list[PatternMatch]] = {}
    out = []
    for variant in variants:
        bases = [m for m in (detect_vcp(bars, variant, daily), *others) if m is not None]
        base_forming = any(m.status == Status.FORMING for m in bases)
        if base_forming not in pivots:
            pivots[base_forming] = detect_pocket_pivots(bars, settings, base_forming=base_forming)
        out.append([*bases, *pivots[base_forming], *gaps])
    return out

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
    if len(bars) < MIN_SESSIONS:
        return []
    multiple = settings.swing_atr_multiple
    daily = with_pending(*zigzag(bars.high, bars.low, atr_threshold(bars.atr14, multiple)))
    weeks = weekly(bars, last_week_complete)
    weekly_swings, _ = zigzag(weeks.high, weeks.low, atr_threshold(weeks.atr14, multiple))
    pullbacks = with_pending(
        *zigzag(bars.high, bars.low, pct_threshold(bars.close, settings.ascending_pullback_min_pct))
    )
    candidates = [
        detect_vcp(bars, settings, daily),
        detect_flat_base(bars, settings, daily),
        detect_cup_with_handle(bars, settings, daily, weeks, weekly_swings),
        detect_ascending_base(bars, settings, pullbacks),
        detect_high_tight_flag(bars, settings, daily),
        detect_three_weeks_tight(bars, settings, weeks),
    ]
    matches = [m for m in candidates if m is not None]
    base_forming = any(m.status == Status.FORMING for m in matches)
    matches += detect_pocket_pivots(bars, settings, base_forming=base_forming)
    matches += detect_earnings_gaps(bars, settings, releases)
    return matches

"""Phase 3 stages of the EOD scan: Fundamentals Grade for every stock, pattern detection for
the stocks that pass the liquidity filters (or for named stocks, filters ignored)."""

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.fundamentals.scan import grade_stocks
from app.patterns.scan import PatternRun, run_patterns
from app.scanner.universe_filter import liquidity_on, passes
from app.settings.schema import AppSettings


async def run_detection(
    session: AsyncSession,
    settings: AppSettings,
    as_of: date,
    ticker_ids: Sequence[int] | None = None,
) -> tuple[dict[str, Any], PatternRun]:
    """Grades and patterns as of `as_of` (a session with bars). Returns job stats and the
    pattern run (whose `matches` list every detection)."""
    stocks = await liquidity_on(session, as_of)
    if ticker_ids is not None:
        wanted = set(ticker_ids)
        graded = [s.ticker_id for s in stocks if s.ticker_id in wanted]
        scanned = graded
    else:
        graded = [s.ticker_id for s in stocks]
        scanned = [s.ticker_id for s in stocks if passes(s, settings)]
    grades = await grade_stocks(session, settings, as_of, graded)
    patterns = await run_patterns(session, settings, as_of, scanned)
    return {"grades": grades, "liquid": len(scanned), "patterns": patterns.stats()}, patterns

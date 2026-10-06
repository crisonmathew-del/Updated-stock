"""A recorded session for the replay tests and the demo: session 333 of the drawn VCP market
(tests.test_setups_pipeline), minute by minute.

SPOT (pivot 92.46, 50-day average volume 860,000) trades below the pivot until 10:15 on 10,000
shares a minute; the 10:15 bar runs from 92.30 to a 92.65 high (the print above the pivot is at
10:15:30, with 465,000 shares done: projected 2.56M, ~298% of average); then either:
- `breakout`: up to a 93.21 high at noon and a 92.75 close on 2.0M shares (the drawn EOD bar:
  the lifecycle confirms the breakout at the close), or
- `fade`: a 92.90 high at 11:00 and a close back below the pivot at 92.10 (rejected).
Every other stock gets its drawn EOD bar as two minutes (09:30 and 15:59), so the close can
run the whole pipeline.
"""

from datetime import date, datetime, time, timedelta
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import MARKET_TZ
from app.models import Ticker
from app.providers.base import MinuteBar
from tests.fakes import make_history
from tests.test_detection_pipeline import DAYS, START

SESSION = 333
PIVOT = 92.46
EARLY_MINUTES = 45  # 09:30-10:14
LATER_MINUTES = 344  # 10:16-15:59
EARLY_VOLUME = 10_000
TRIGGER_VOLUME = 20_000
DAY_VOLUME = 2_000_000


def _path(points: list[tuple[int, float]], n: int) -> list[float]:
    out = []
    for i in range(n):
        for (a, pa), (b, pb) in pairwise(points):
            if a <= i <= b:
                out.append(pa + (pb - pa) * (i - a) / (b - a))
                break
    return out


def spot_minutes(day: date, *, fade: bool = False) -> list[MinuteBar]:
    start = datetime.combine(day, time(9, 30), MARKET_TZ)
    bars = []
    for i in range(EARLY_MINUTES):
        o = 91.5 + 0.8 * i / (EARLY_MINUTES - 1)
        c = o + 0.01
        low = 91.04 if i == 15 else o - 0.03
        bars.append(
            MinuteBar("SPOT", start + timedelta(minutes=i), o, c + 0.03, low, c, EARLY_VOLUME)
        )
    bars.append(
        MinuteBar("SPOT", start + timedelta(minutes=45), 92.30, 92.65, 92.28, 92.60, TRIGGER_VOLUME)
    )
    peak, close, peak_at = (92.90, 92.10, 44) if fade else (93.21, 92.75, 104)
    closes = _path([(0, 92.6), (peak_at, peak), (LATER_MINUTES - 1, close)], LATER_MINUTES)
    rest = DAY_VOLUME - EARLY_MINUTES * EARLY_VOLUME - TRIGGER_VOLUME
    each, extra = divmod(rest, LATER_MINUTES)
    previous = 92.60
    for i, c in enumerate(closes):
        top = max(previous, c)
        high = peak if i == peak_at else min(top + 0.02, peak)
        bars.append(
            MinuteBar(
                "SPOT",
                start + timedelta(minutes=46 + i),
                previous,
                high,
                min(previous, c) - 0.02,
                c,
                each + (extra if i == LATER_MINUTES - 1 else 0),
            )
        )
        previous = c
    return bars


async def recording(session: AsyncSession, *, fade: bool = False) -> list[MinuteBar]:
    day = DAYS[SESSION]
    bars = spot_minutes(day, fade=fade)
    symbols = (await session.scalars(select(Ticker.symbol).where(Ticker.symbol != "SPOT"))).all()
    first = datetime.combine(day, time(9, 30), MARKET_TZ)
    last = datetime.combine(day, time(15, 59), MARKET_TZ)
    for symbol in symbols:
        b = make_history(symbol, START, day, first_close=400, step=0.2).bars[SESSION]
        half = b.volume // 2
        bars.append(MinuteBar(symbol, first, b.open, b.high, b.low, b.open, half))
        bars.append(
            MinuteBar(
                symbol,
                last,
                b.open,
                max(b.open, b.close),
                min(b.open, b.close),
                b.close,
                b.volume - half,
            )
        )
    return bars

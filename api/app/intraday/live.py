"""What counts as live: quotes and scan results from a session after the latest one the EOD
scan has processed. Before today's close is processed, that's today's trading; after it,
nothing is live (the close is the price). A replay of an older recorded session against a
database that ends the day before it counts as live in the same way."""

import json
from collections.abc import Awaitable, Sequence
from datetime import date, datetime
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import MARKET_TZ
from app.models import IndicatorDaily

QUOTES_KEY = "live:quotes"
EVENTS_KEY = "live:setup_events"  # newest first, capped
MAX_EVENTS = 200
REFRESH_CHANNEL = "watch:refresh"


def scan_key(scan: str, day: date | str) -> str:
    return f"scan:{scan}:{day if isinstance(day, str) else day.isoformat()}"


def session_of(iso: str) -> date:
    return datetime.fromisoformat(iso).astimezone(MARKET_TZ).date()


async def eod_through(session: AsyncSession) -> date | None:
    """The latest session with analytics: anything after it is live."""
    return await session.scalar(select(func.max(IndicatorDaily.date)))


def is_live(at: str | None, through: date | None) -> bool:
    return bool(at) and (through is None or session_of(cast(str, at)) > through)


async def live_quotes(
    redis: Redis, through: date | None, symbols: Sequence[str] | None = None
) -> dict[str, dict[str, Any]]:
    if symbols is not None:
        if not symbols:
            return {}
        values = await cast(Awaitable[list[str | None]], redis.hmget(QUOTES_KEY, list(symbols)))
        raw = {s: v for s, v in zip(symbols, values, strict=True) if v}
    else:
        raw = await cast(Awaitable[dict[str, str]], redis.hgetall(QUOTES_KEY))
    quotes = {}
    for symbol, value in raw.items():
        quote = json.loads(value)
        if quote.get("last") is not None and is_live(quote.get("at"), through):
            quotes[symbol] = quote
    return quotes


async def latest_scan(redis: Redis, scan: str, through: date | None) -> dict[str, Any] | None:
    body = await redis.get(scan_key(scan, "latest"))
    if not body:
        return None
    result: dict[str, Any] = json.loads(body)
    return result if is_live(result.get("at"), through) else None


async def live_events(redis: Redis, through: date | None) -> list[dict[str, Any]]:
    """This session's setup events (provisional breakouts, past the buy zone, stop hits)."""
    raw = await cast(Awaitable[list[str]], redis.lrange(EVENTS_KEY, 0, MAX_EVENTS - 1))
    events = [json.loads(r) for r in raw]
    return [e for e in events if is_live(e.get("at"), through)]

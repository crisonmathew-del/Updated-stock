"""Live data (spec §6.8, §8): the WebSocket that pushes alerts, quotes and setup events to open
pages; the latest quotes and scan results for a page that just opened; and a stock's intraday
bars for the 1- and 5-minute chart.

- WS     /ws                         authenticated by the session cookie
- GET    /live                        {session, quotes, events, premarket, sweep}
- GET    /stocks/{symbol}/intraday    ?interval=1|5&date=YYYY-MM-DD (default: latest stored)

The socket batches: everything published in a 250 ms window goes out as one message
(`{"type": "batch", "events": [...]}`), with only the latest quote per stock. Alerts go only to
their user. The session is checked again every minute, so signing out closes the socket.
The session cookie is SameSite=Lax, which browsers don't send on a cross-site WebSocket
handshake: another site can't open this socket as you.
"""

import asyncio
import contextlib
import datetime as dt
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.alerts.engine import LIVE_CHANNEL
from app.api.deps import DbSession, RedisClient, current_user
from app.core.calendar import MARKET_TZ
from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import SESSION_COOKIE, resolve_session
from app.data.loaders import query_frame
from app.intraday.live import eod_through, latest_scan, live_events, live_quotes, session_of
from app.models import Alert, DailyBar, IntradayBar, Ticker

log = get_logger(__name__)

router = APIRouter(tags=["live"])
BATCH_SECONDS = 0.25
RECHECK_SECONDS = 60
INTERVALS = (1, 5)


class LiveOut(BaseModel):
    session: dt.date
    through: dt.date | None  # the latest processed close: a newer one means pages are stale
    quotes: dict[str, dict[str, Any]]
    events: list[dict[str, Any]]  # this session's setup events, newest first
    premarket: dict[str, Any] | None
    sweep: dict[str, Any] | None


class IntradayOut(BaseModel):
    symbol: str
    date: dt.date | None
    interval: int
    prev_close: float | None
    open_time: int | None  # 09:30 US/Eastern as epoch seconds (pre-market bars come before)
    time: list[int]  # bar start, epoch seconds
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[int]
    sessions: list[dt.date]  # the days with stored minute bars (newest first)


def _today() -> dt.date:
    return dt.datetime.now(MARKET_TZ).date()


@router.get("/live", dependencies=[Depends(current_user)])
async def live(db: DbSession, redis: RedisClient) -> LiveOut:
    """Quotes and scans newer than the latest processed close (see app.intraday.live)."""
    through = await eod_through(db)
    quotes = await live_quotes(redis, through)
    sessions = [session_of(q["at"]) for q in quotes.values()]
    return LiveOut(
        session=max(sessions) if sessions else _today(),
        through=through,
        quotes=quotes,
        events=await live_events(redis, through),
        premarket=await latest_scan(redis, "premarket", through),
        sweep=await latest_scan(redis, "sweep", through),
    )


@router.get(
    "/stocks/{symbol}/intraday", dependencies=[Depends(current_user)], response_model=IntradayOut
)
async def intraday(
    db: DbSession,
    symbol: str,
    interval: int = 1,
    day: Annotated[dt.date | None, Query(alias="date")] = None,
) -> IntradayOut:
    if interval not in INTERVALS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The interval is 1 or 5 minutes."
        )
    ticker = await db.scalar(select(Ticker).where(Ticker.symbol == symbol.strip().upper()))
    if ticker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No stock {symbol.upper()}.")
    days = await query_frame(
        db,
        "SELECT DISTINCT (ts AT TIME ZONE 'America/New_York')::date AS d FROM intraday_bars "
        f"WHERE ticker_id = {int(ticker.id)} ORDER BY d DESC LIMIT 30",
    )
    sessions: list[dt.date] = days["d"].to_list() if days.height else []
    chosen = day or (sessions[0] if sessions else None)
    empty = IntradayOut(
        symbol=ticker.symbol,
        date=chosen,
        interval=interval,
        prev_close=None,
        open_time=None,
        time=[],
        open=[],
        high=[],
        low=[],
        close=[],
        volume=[],
        sessions=sessions,
    )
    if chosen is None:
        return empty
    start = dt.datetime.combine(chosen, dt.time(0), MARKET_TZ)
    end = dt.datetime.combine(chosen, dt.time(23, 59, 59), MARKET_TZ)
    rows = (
        await db.execute(
            select(
                IntradayBar.ts,
                IntradayBar.open,
                IntradayBar.high,
                IntradayBar.low,
                IntradayBar.close,
                IntradayBar.volume,
            )
            .where(
                IntradayBar.ticker_id == ticker.id,
                IntradayBar.ts >= start,
                IntradayBar.ts <= end,
            )
            .order_by(IntradayBar.ts)
        )
    ).all()
    prev_close = await db.scalar(
        select(DailyBar.close)
        .where(DailyBar.ticker_id == ticker.id, DailyBar.date < chosen)
        .order_by(DailyBar.date.desc())
        .limit(1)
    )
    bars: list[list[Any]] = []
    for ts, o, h, lo, c, v in rows:
        t = int(ts.timestamp())
        bucket = t - t % (interval * 60)
        if bars and bars[-1][0] == bucket:
            last = bars[-1]
            last[2], last[3], last[4] = max(last[2], h), min(last[3], lo), c
            last[5] += int(v)
        else:
            bars.append([bucket, o, h, lo, c, int(v)])
    return empty.model_copy(
        update={
            "prev_close": prev_close,
            "open_time": int(dt.datetime.combine(chosen, dt.time(9, 30), MARKET_TZ).timestamp()),
            "time": [b[0] for b in bars],
            "open": [b[1] for b in bars],
            "high": [b[2] for b in bars],
            "low": [b[3] for b in bars],
            "close": [b[4] for b in bars],
            "volume": [b[5] for b in bars],
        }
    )


def merge(events: list[dict[str, Any]], user_id: int) -> list[dict[str, Any]]:
    """One window's events for one user: their alerts, and the latest quote per stock."""
    out: list[dict[str, Any]] = []
    quotes: dict[str, dict[str, Any]] = {}
    for event in events:
        kind = event.get("type")
        if kind == "alert":
            if event.get("user_id") == user_id:
                out.append({"type": "alert", "data": event["data"]})
        elif kind == "quotes":
            for q in event.get("data", []):
                quotes[q["symbol"]] = q
        else:
            out.append(event)
    if quotes:
        out.append({"type": "quotes", "data": list(quotes.values())})
    return out


async def _unread(user_id: int) -> int:
    async with get_sessionmaker()() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.user_id == user_id, Alert.read_at.is_(None))
        )
    return int(count or 0)


@router.websocket("/ws")
async def live_socket(ws: WebSocket) -> None:
    redis: Redis = get_redis()
    token = ws.cookies.get(SESSION_COOKIE)
    user = await resolve_session(redis, token) if token else None
    if user is None:
        await ws.close(code=4401, reason="Sign in to continue.")
        return
    await ws.accept()
    pubsub = redis.pubsub()
    await pubsub.subscribe(LIVE_CHANNEL)
    pending: list[dict[str, Any]] = []
    await ws.send_json({"type": "hello", "unread": await _unread(user.id)})

    async def receive() -> None:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                with contextlib.suppress(ValueError):
                    pending.append(json.loads(message["data"]))

    async def send() -> None:
        checked = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(BATCH_SECONDS)
            if pending:
                events = merge(pending[:], user.id)
                pending.clear()
                if events:
                    await ws.send_json({"type": "batch", "events": events})
            now = asyncio.get_running_loop().time()
            if now - checked >= RECHECK_SECONDS:
                checked = now
                if token is None or await resolve_session(redis, token) is None:
                    await ws.close(code=4401, reason="Signed out.")
                    return

    async def client() -> None:
        while True:
            message = await ws.receive_json()
            if isinstance(message, dict) and message.get("type") == "ping":
                await ws.send_json({"type": "pong"})

    tasks = [asyncio.create_task(t()) for t in (receive, send, client)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                log.warning("live.socket_error", error=str(exc))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pubsub.aclose()  # type: ignore[no-untyped-call]

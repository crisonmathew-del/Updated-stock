"""Live data over HTTP and the WebSocket: today's quotes and scans, a stock's intraday bars
(1 and 5 minutes), and the socket's batching, per-user alerts and sign-in check."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest
import uvicorn
import websockets
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.engine import LIVE_CHANNEL
from app.api.routes.live import merge
from app.core.calendar import MARKET_TZ
from app.core.redis import get_redis
from app.core.security import SESSION_COOKIE, create_session
from app.data.universe import plan_universe, sync_tickers
from app.intraday.live import EVENTS_KEY, QUOTES_KEY, scan_key
from app.intraday.store import save_bars, ticker_ids
from app.main import app as fastapi_app
from app.models import IndicatorDaily, User
from app.providers.base import MinuteBar
from tests.test_universe import listed

DAY = date(2026, 10, 2)


def test_merge_keeps_your_alerts_and_the_latest_quote_per_stock() -> None:
    events: list[dict[str, Any]] = [
        {"type": "alert", "user_id": 1, "data": {"id": 10}},
        {"type": "alert", "user_id": 2, "data": {"id": 11}},
        {"type": "quotes", "data": [{"symbol": "SPOT", "last": 92.5}]},
        {"type": "setup_event", "data": {"kind": "breakout_provisional", "symbol": "SPOT"}},
        {
            "type": "quotes",
            "data": [{"symbol": "SPOT", "last": 92.7}, {"symbol": "AAPL", "last": 1}],
        },
    ]
    assert merge(events, 1) == [
        {"type": "alert", "data": {"id": 10}},
        {"type": "setup_event", "data": {"kind": "breakout_provisional", "symbol": "SPOT"}},
        {
            "type": "quotes",
            "data": [{"symbol": "SPOT", "last": 92.7}, {"symbol": "AAPL", "last": 1}],
        },
    ]


@pytest.mark.integration
async def test_live_means_newer_than_the_latest_processed_close(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    now = datetime.now(MARKET_TZ)
    # The EOD scan has processed the session two days ago.
    await sync_tickers(db, plan_universe(listed("SPOT")), DAY)
    ids = await ticker_ids(db, ["SPOT"])
    db.add(IndicatorDaily(ticker_id=ids["SPOT"], date=(now - timedelta(days=2)).date()))
    await db.commit()
    redis = get_redis()
    fresh = {"symbol": "SPOT", "last": 92.7, "at": now.isoformat()}
    stale = {"symbol": "OLD", "last": 1.0, "at": (now - timedelta(days=3)).isoformat()}
    await redis.hset(QUOTES_KEY, mapping={"SPOT": json.dumps(fresh), "OLD": json.dumps(stale)})  # type: ignore[misc]
    scan = {"at": now.isoformat(), "items": [{"symbol": "NVDA", "change_pct": 5.0}]}
    await redis.set(scan_key("premarket", "latest"), json.dumps(scan))
    old_scan = {"at": (now - timedelta(days=3)).isoformat(), "items": []}
    await redis.set(scan_key("sweep", "latest"), json.dumps(old_scan))
    event = {"kind": "breakout_provisional", "symbol": "SPOT", "at": now.isoformat()}
    old_event = {"kind": "setup_stop", "symbol": "OLD", "at": (now - timedelta(days=3)).isoformat()}
    await redis.lpush(EVENTS_KEY, json.dumps(old_event), json.dumps(event))  # type: ignore[misc]
    body = (await signed_in.get("/api/live")).json()
    assert body["session"] == now.date().isoformat()
    assert [e["symbol"] for e in body["events"]] == ["SPOT"]
    assert list(body["quotes"]) == ["SPOT"]
    assert body["premarket"]["items"][0]["symbol"] == "NVDA"
    assert body["sweep"] is None


@pytest.mark.integration
async def test_intraday_bars_in_one_and_five_minutes(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT")), DAY)
    ids = await ticker_ids(db, ["SPOT"])
    open_ = datetime(2026, 10, 2, 9, 30, tzinfo=MARKET_TZ)
    bars = [
        MinuteBar("SPOT", open_ + timedelta(minutes=i), 10 + i, 11 + i, 9 + i, 10.5 + i, 100)
        for i in range(7)
    ]
    await save_bars(db, bars, ids, "test")
    one = (await signed_in.get("/api/stocks/spot/intraday")).json()
    assert (one["date"], one["interval"], len(one["time"])) == ("2026-10-02", 1, 7)
    assert one["open_time"] == int(open_.timestamp())
    assert one["sessions"] == ["2026-10-02"]
    five = (await signed_in.get("/api/stocks/SPOT/intraday", params={"interval": 5})).json()
    # 09:30-09:34 and 09:35-09:36.
    assert five["time"] == [int(open_.timestamp()), int(open_.timestamp()) + 300]
    assert (five["open"], five["high"], five["low"], five["close"], five["volume"]) == (
        [10, 15],
        [15, 17],
        [9, 14],
        [14.5, 16.5],
        [500, 200],
    )
    other = (await signed_in.get("/api/stocks/SPOT/intraday", params={"date": "2026-10-01"})).json()
    assert other["time"] == []
    missing = await signed_in.get("/api/stocks/NOPE/intraday")
    assert missing.status_code == 404
    odd = await signed_in.get("/api/stocks/SPOT/intraday", params={"interval": 3})
    assert odd.status_code == 422


@pytest.fixture
async def server() -> AsyncIterator[int]:
    config = uvicorn.Config(
        fastapi_app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"
    )
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    for _ in range(250):
        if srv.started:
            break
        await asyncio.sleep(0.02)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield port
    srv.should_exit = True
    await task


@pytest.mark.integration
async def test_the_socket_needs_a_session_and_batches_events(
    server: int, db: AsyncSession, user: User
) -> None:
    url = f"ws://127.0.0.1:{server}/api/ws"
    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(url):
            pass
    token = await create_session(get_redis(), user)
    async with websockets.connect(
        url, additional_headers={"Cookie": f"{SESSION_COOKIE}={token}"}
    ) as ws:
        hello = json.loads(await ws.recv())
        assert hello == {"type": "hello", "unread": 0}
        redis = get_redis()
        for event in (
            {"type": "alert", "user_id": user.id, "data": {"id": 1, "title": "Yours"}},
            {"type": "alert", "user_id": user.id + 1, "data": {"id": 2, "title": "Not yours"}},
            {"type": "quotes", "data": [{"symbol": "SPOT", "last": 92.5}]},
            {"type": "quotes", "data": [{"symbol": "SPOT", "last": 92.6}]},
        ):
            await redis.publish(LIVE_CHANNEL, json.dumps(event))
        # Everything published within one 250 ms window arrives as one batch (the publishes
        # above could straddle two windows, so read until the last quote is in).
        events: list[dict[str, object]] = []
        while not any(e.get("data") == [{"symbol": "SPOT", "last": 92.6}] for e in events):
            batch = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert batch["type"] == "batch"
            events += batch["events"]
        assert [e["data"] for e in events if e["type"] == "alert"] == [{"id": 1, "title": "Yours"}]
        assert len(events) <= 3
        await ws.send(json.dumps({"type": "ping"}))
        assert json.loads(await asyncio.wait_for(ws.recv(), 3)) == {"type": "pong"}

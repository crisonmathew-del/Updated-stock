"""The Alpaca adapter against a local stand-in for its stream and REST API."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from app.core.calendar import MARKET_TZ
from app.providers.alpaca import AlpacaStream, parse_time, snapshot_from, trades_in
from app.providers.base import ProviderError


def test_times_and_messages() -> None:
    assert parse_time("2026-10-02T14:31:05.123456789Z") == datetime(
        2026, 10, 2, 14, 31, 5, 123456, tzinfo=UTC
    )
    assert parse_time("2026-10-02T14:31:05Z") == datetime(2026, 10, 2, 14, 31, 5, tzinfo=UTC)
    trades, control = trades_in(
        '[{"T":"success","msg":"connected"},'
        '{"T":"t","S":"SPOT","p":92.6,"s":300,"t":"2026-10-02T14:31:05.1Z","x":"V"}]'
    )
    assert [(t.symbol, t.price, t.size) for t in trades] == [("SPOT", 92.6, 300)]
    assert control == [{"T": "success", "msg": "connected"}]


def test_snapshots_split_premarket_from_the_session() -> None:
    data = {
        "latestTrade": {"t": "2026-10-02T12:05:00Z", "p": 97.1, "s": 100},
        "dailyBar": {
            "t": "2026-10-02T04:00:00Z",
            "o": 96,
            "h": 97.5,
            "l": 95.8,
            "c": 97.1,
            "v": 42_000,
        },
        "prevDailyBar": {
            "t": "2026-10-01T04:00:00Z",
            "o": 91,
            "h": 92,
            "l": 90,
            "c": 92.75,
            "v": 1,
        },
    }
    before_open = datetime(2026, 10, 2, 8, 5, tzinfo=MARKET_TZ)
    pre = snapshot_from("SPOT", data, before_open)
    assert (pre.last, pre.prev_close, pre.premarket_volume, pre.volume, pre.open) == (
        97.1,
        92.75,
        42_000,
        0,
        None,
    )
    during = snapshot_from("SPOT", data, datetime(2026, 10, 2, 11, 0, tzinfo=MARKET_TZ))
    assert (during.volume, during.premarket_volume, during.open, during.high) == (
        42_000,
        0,
        96,
        97.5,
    )
    # Yesterday's daily bar (before today's first trade) isn't today's session.
    stale = snapshot_from("SPOT", data, datetime(2026, 10, 5, 10, 0, tzinfo=MARKET_TZ))
    assert (stale.volume, stale.open) == (0, None)


class FakeAlpaca:
    """Speaks just enough of Alpaca's stream protocol."""

    def __init__(self, key: str = "key") -> None:
        self.key = key
        self.subscribed: set[str] = set()
        self.received: list[dict[str, Any]] = []
        self.connections = 0
        self.ready = asyncio.Event()
        self.drop_after_first = False

    async def handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        await ws.send(json.dumps([{"T": "success", "msg": "connected"}]))
        auth = json.loads(await ws.recv())
        self.received.append(auth)
        if auth.get("key") != self.key:
            await ws.send(json.dumps([{"T": "error", "code": 402, "msg": "auth failed"}]))
            return
        await ws.send(json.dumps([{"T": "success", "msg": "authenticated"}]))
        if self.drop_after_first and self.connections == 1:
            await ws.close()
            return
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            if msg["action"] == "subscribe":
                self.subscribed |= set(msg["trades"])
            else:
                self.subscribed -= set(msg["trades"])
            self.ready.set()
            trades = [
                {"T": "t", "S": s, "p": 100.0, "s": 10, "t": "2026-10-02T14:31:05Z"}
                for s in sorted(self.subscribed)
            ]
            await ws.send(json.dumps(trades))


@asynccontextmanager
async def fake_server(fake: FakeAlpaca) -> AsyncIterator[str]:
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


async def test_stream_authenticates_subscribes_and_follows_changes() -> None:
    fake = FakeAlpaca()
    async with fake_server(fake) as url:
        feed = AlpacaStream("key", "secret", stream_url=url)
        await feed.subscribe(["spot"])
        stream = feed.trades()
        first = await asyncio.wait_for(anext(stream), 5)
        assert (first.symbol, first.price) == ("SPOT", 100.0)
        assert fake.received[0] == {"action": "auth", "key": "key", "secret": "secret"}
        # Adding AAPL sends one subscribe; the fake answers with a print for each name.
        await feed.subscribe(["SPOT", "AAPL"])
        symbols = {(await asyncio.wait_for(anext(stream), 5)).symbol for _ in range(2)}
        assert symbols == {"SPOT", "AAPL"}
        assert {"action": "subscribe", "trades": ["AAPL"]} in fake.received
        await feed.subscribe(["AAPL"])
        await asyncio.wait_for(anext(stream), 5)
        assert {"action": "unsubscribe", "trades": ["SPOT"]} in fake.received
        await feed.aclose()


async def test_stream_reconnects_after_a_drop() -> None:
    fake = FakeAlpaca()
    fake.drop_after_first = True
    async with fake_server(fake) as url:
        feed = AlpacaStream("key", "secret", stream_url=url)
        await feed.subscribe(["SPOT"])
        trade = await asyncio.wait_for(anext(feed.trades()), 10)
        assert trade.symbol == "SPOT"
        assert fake.connections == 2
        await feed.aclose()


async def test_wrong_keys_stop_with_a_clear_error() -> None:
    async with fake_server(FakeAlpaca(key="right")) as url:
        feed = AlpacaStream("wrong", "secret", stream_url=url)
        with pytest.raises(ProviderError, match=r"auth failed.*ALPACA_API_KEY_ID"):
            await asyncio.wait_for(anext(feed.trades()), 5)
        await feed.aclose()


class NoLimit:
    async def acquire(self, cost: float = 1.0) -> None:
        return None


async def test_snapshots_are_fetched_in_chunks() -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        assert request.headers["APCA-API-KEY-ID"] == "key"
        symbols = request.url.params["symbols"].split(",")
        body = {
            s: {
                "latestTrade": {"t": "2026-10-02T14:00:00Z", "p": 10.0, "s": 1},
                "prevDailyBar": {"t": "2026-10-01T04:00:00Z", "c": 9.5},
            }
            for s in symbols
        }
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        headers={"APCA-API-KEY-ID": "key", "APCA-API-SECRET-KEY": "secret"},
    )
    feed = AlpacaStream("key", "secret", client=client, limiter=NoLimit())  # type: ignore[arg-type]
    symbols = [f"S{i:03d}" for i in range(150)]
    snaps = await feed.snapshots(symbols)
    assert len(snaps) == 150
    assert len(requested) == 2  # 100 + 50
    assert "feed=iex" in requested[0]
    assert (snaps["S000"].last, snaps["S000"].prev_close) == (10.0, 9.5)
    await feed.aclose()

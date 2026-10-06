"""Alpaca market data (spec §5.2): real-time trades over WebSocket and snapshots over REST.

The free plan streams IEX only: a small share of the market's volume, so intraday volume is
scaled by `volume_share` and every intraday volume check stays provisional until the close
confirms it. A paid plan (`ALPACA_FEED=sip`) sees full consolidated volume.

Stream protocol (v2): on connect the server sends `[{"T":"success","msg":"connected"}]`; we
send `{"action":"auth","key","secret"}` and get `authenticated` (or an error); then
`{"action":"subscribe","trades":[...]}` / `unsubscribe`. Trades arrive in arrays as
`{"T":"t","S":symbol,"p":price,"s":size,"t":RFC 3339 time}`.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.intraday.session import Phase, phase, session_date
from app.providers import http
from app.providers.base import ProviderError, Snapshot, StreamProvider, Trade

log = get_logger(__name__)

STREAM_URL = "wss://stream.data.alpaca.markets/v2/{feed}"
DATA_URL = "https://data.alpaca.markets/v2"
SNAPSHOT_CHUNK = 100
REQUESTS_PER_SECOND = 3  # the free plan allows 200 requests a minute
MAX_BACKOFF_SECONDS = 30


def parse_time(value: str) -> datetime:
    """RFC 3339 with up to nanoseconds ("2026-10-02T14:31:05.123456789Z")."""
    head, _, rest = value.rstrip("Z").partition(".")
    fraction = (rest + "000000")[:6] if rest else "000000"
    return datetime.fromisoformat(f"{head}.{fraction}+00:00")


def trades_in(message: str | bytes) -> tuple[list[Trade], list[dict[str, Any]]]:
    """The trades in one stream message, and its control entries (success, error, …)."""
    entries = json.loads(message)
    if isinstance(entries, dict):
        entries = [entries]
    trades: list[Trade] = []
    control: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("T") == "t":
            trades.append(
                Trade(
                    symbol=str(entry["S"]),
                    timestamp=parse_time(str(entry["t"])),
                    price=float(entry["p"]),
                    size=int(entry["s"]),
                )
            )
        else:
            control.append(entry)
    return trades, control


def snapshot_from(symbol: str, data: dict[str, Any], now: datetime) -> Snapshot:
    """Alpaca's snapshot → ours. Before the open, today's daily bar holds pre-market trading
    (when the feed reports it); during and after the session it is the regular session."""
    trade = data.get("latestTrade") or {}
    daily = data.get("dailyBar") or {}
    prev = data.get("prevDailyBar") or {}
    ts = parse_time(trade["t"]) if trade.get("t") else None
    # Daily bars are stamped at midnight US/Eastern (04:00 or 05:00 UTC).
    bar_day = session_date(parse_time(daily["t"])) if daily.get("t") else None
    today = bar_day == session_date(now)
    premarket = phase(now) is Phase.PREMARKET
    return Snapshot(
        symbol=symbol,
        ts=ts,
        last=float(trade["p"]) if "p" in trade else None,
        prev_close=float(prev["c"]) if "c" in prev else None,
        open=float(daily["o"]) if today and not premarket and "o" in daily else None,
        high=float(daily["h"]) if today and not premarket and "h" in daily else None,
        low=float(daily["l"]) if today and not premarket and "l" in daily else None,
        volume=int(daily.get("v", 0)) if today and not premarket else 0,
        premarket_volume=int(daily.get("v", 0)) if today and premarket else 0,
    )


class AlpacaStream(StreamProvider):
    name: ClassVar[str] = "alpaca"

    def __init__(
        self,
        key_id: str,
        secret: str,
        feed: str = "iex",
        *,
        stream_url: str | None = None,
        data_url: str = DATA_URL,
        client: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._key = key_id
        self._secret = secret
        self._feed = feed
        self._stream_url = stream_url or STREAM_URL.format(feed=feed)
        self._data_url = data_url.rstrip("/")
        self.partial_volume = feed == "iex"
        self._client = client or http.new_client(
            "breakout", headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        )
        self._limiter = limiter
        self._symbols: set[str] = set()
        self._ws: ClientConnection | None = None
        self._closed = False

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def subscribe(self, symbols: Sequence[str]) -> None:
        wanted = {s.upper() for s in symbols}
        added, removed = sorted(wanted - self._symbols), sorted(self._symbols - wanted)
        self._symbols = wanted
        if self._ws is None:
            return  # sent on (re)connect
        if removed:
            await self._ws.send(json.dumps({"action": "unsubscribe", "trades": removed}))
        if added:
            await self._ws.send(json.dumps({"action": "subscribe", "trades": added}))

    async def trades(self) -> AsyncIterator[Trade]:
        delay = 1.0
        while not self._closed:
            try:
                async with websockets.connect(self._stream_url, max_size=2**22) as ws:
                    await self._handshake(ws)
                    self._ws = ws
                    delay = 1.0
                    async for message in ws:
                        trades, control = trades_in(message)
                        for entry in control:
                            if entry.get("T") == "error":
                                raise ProviderError(f"Alpaca stream: {entry.get('msg')}")
                        for trade in trades:
                            yield trade
            except (OSError, websockets.ConnectionClosed, ProviderError) as exc:
                if self._closed:
                    break
                if isinstance(exc, ProviderError) and "auth" in str(exc).lower():
                    raise  # wrong keys: retrying won't help
                log.warning("alpaca.stream_reconnect", error=str(exc), delay=delay)
            finally:
                self._ws = None
            await asyncio.sleep(delay)
            delay = min(MAX_BACKOFF_SECONDS, delay * 2)

    async def _handshake(self, ws: ClientConnection) -> None:
        await self._expect(ws, "connected")
        await ws.send(json.dumps({"action": "auth", "key": self._key, "secret": self._secret}))
        await self._expect(ws, "authenticated")
        if self._symbols:
            await ws.send(json.dumps({"action": "subscribe", "trades": sorted(self._symbols)}))

    @staticmethod
    async def _expect(ws: ClientConnection, msg: str) -> None:
        _, control = trades_in(await ws.recv())
        for entry in control:
            if entry.get("T") == "error":
                raise ProviderError(
                    f"Alpaca stream auth failed: {entry.get('msg')} (code {entry.get('code')}). "
                    "Check ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY."
                )
            if entry.get("T") == "success" and entry.get("msg") == msg:
                return
        raise ProviderError(f"Alpaca stream: expected '{msg}', got {control}")

    async def snapshots(self, symbols: Sequence[str]) -> dict[str, Snapshot]:
        if self._limiter is None:
            self._limiter = RateLimiter(get_redis(), "alpaca", per_second=REQUESTS_PER_SECOND)
        now = self.now()
        out: dict[str, Snapshot] = {}
        unique = sorted({s.upper() for s in symbols})
        for start in range(0, len(unique), SNAPSHOT_CHUNK):
            chunk = unique[start : start + SNAPSHOT_CHUNK]
            url = f"{self._data_url}/stocks/snapshots?symbols={','.join(chunk)}&feed={self._feed}"
            response = await http.get(self._client, url, limiter=self._limiter)
            if response.status_code == 404:
                continue
            body: dict[str, Any] = response.json()
            for symbol, data in body.items():
                if isinstance(data, dict):
                    out[symbol] = snapshot_from(symbol, data, now)
        return out

    async def aclose(self) -> None:
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
        await self._client.aclose()

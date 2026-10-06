"""Replay mode (spec §15, Phase 6): plays a recorded session of one-minute bars back as a live
feed, at any speed, on the session's own clock.

Each minute bar becomes four prints (open; the high and the low in the order the bar implies;
the close) at :00, :15, :30 and :45, with the volume split between them, so the bars the
watcher rebuilds are exactly the recorded ones. Recordings are CSV (optionally gzipped) with
columns symbol, ts (ISO 8601 with offset, the minute's start), open, high, low, close, volume:
`streamer` writes them from live sessions (`record` CLI) and the acceptance test ships one.
"""

import asyncio
import csv
import gzip
import io
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

from app.intraday.day import DayState
from app.intraday.session import at, session_date
from app.providers.base import MinuteBar, Snapshot, StreamProvider, Trade

COLUMNS = ("symbol", "ts", "open", "high", "low", "close", "volume")
OFFSETS = (0, 15, 30, 45)  # seconds into the minute of the four prints


def ticks_from_bar(bar: MinuteBar) -> list[Trade]:
    """open → (low, high) on an up bar or (high, low) on a down bar → close."""
    first, second = (bar.low, bar.high) if bar.close >= bar.open else (bar.high, bar.low)
    share, rest = divmod(bar.volume, 4)
    sizes = (share, share, share, share + rest)
    prices = (bar.open, first, second, bar.close)
    return [
        Trade(bar.symbol, bar.ts + timedelta(seconds=offset), price, size)
        for offset, price, size in zip(OFFSETS, prices, sizes, strict=True)
    ]


def read_recording(path: Path) -> list[MinuteBar]:
    raw = path.read_bytes()
    text = gzip.decompress(raw).decode() if path.suffix == ".gz" else raw.decode()
    return parse_recording(text)


def parse_recording(text: str) -> list[MinuteBar]:
    reader = csv.DictReader(io.StringIO(text))
    missing = set(COLUMNS) - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"The recording is missing column(s): {', '.join(sorted(missing))}.")
    bars = [
        MinuteBar(
            symbol=row["symbol"].strip().upper(),
            ts=datetime.fromisoformat(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(float(row["volume"])),
        )
        for row in reader
    ]
    for bar in bars:
        if bar.ts.tzinfo is None:
            raise ValueError(f"Timestamp {bar.ts} for {bar.symbol} has no UTC offset.")
    return sorted(bars, key=lambda b: (b.ts, b.symbol))


def format_recording(bars: Iterable[MinuteBar]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for b in sorted(bars, key=lambda b: (b.ts, b.symbol)):
        writer.writerow([b.symbol, b.ts.isoformat(), b.open, b.high, b.low, b.close, b.volume])
    return out.getvalue()


def write_recording(bars: Iterable[MinuteBar], path: Path) -> None:
    text = format_recording(bars)
    data = gzip.compress(text.encode()) if path.suffix == ".gz" else text.encode()
    path.write_bytes(data)


class ReplayStream(StreamProvider):
    """Plays `bars` back. `speed` is how many times faster than real time (0 = as fast as
    possible); `start` ("HH:MM" US/Eastern) skips quickly through everything before it."""

    name: ClassVar[str] = "replay"

    def __init__(
        self,
        bars: Sequence[MinuteBar],
        *,
        speed: float = 60,
        prev_closes: dict[str, float] | None = None,
        start: str | None = None,
    ) -> None:
        if not bars:
            raise ValueError("The recording has no bars.")
        self._ticks = sorted(
            (t for b in bars for t in ticks_from_bar(b)), key=lambda t: (t.timestamp, t.symbol)
        )
        self._speed = speed
        self._prev_closes = dict(prev_closes or {})
        day = session_date(self._ticks[0].timestamp)
        self._start = at(day, start) if start else None
        self._clock = self._ticks[0].timestamp
        self._subscribed: set[str] = set()
        self._days: dict[str, DayState] = {}
        self.finished = asyncio.Event()

    @property
    def symbols(self) -> list[str]:
        return sorted({t.symbol for t in self._ticks})

    @property
    def session(self) -> datetime:
        return self._ticks[0].timestamp

    def now(self) -> datetime:
        return self._clock

    async def subscribe(self, symbols: Sequence[str]) -> None:
        self._subscribed = {s.upper() for s in symbols}

    async def trades(self) -> AsyncIterator[Trade]:
        previous: datetime | None = None
        for tick in self._ticks:
            # Before `start` (and across the jump to it) prints go out without pacing.
            fast = self._start is not None and (
                tick.timestamp < self._start or previous is None or previous < self._start
            )
            if self._speed > 0 and previous is not None and not fast:
                await asyncio.sleep((tick.timestamp - previous).total_seconds() / self._speed)
            else:
                await asyncio.sleep(0)  # let the rest of the streamer run
            previous = tick.timestamp
            self._clock = tick.timestamp
            self._days.setdefault(tick.symbol, DayState(tick.symbol)).add(tick)
            if tick.symbol in self._subscribed:
                yield tick
        self.finished.set()

    async def snapshots(self, symbols: Sequence[str]) -> dict[str, Snapshot]:
        out: dict[str, Snapshot] = {}
        for symbol in symbols:
            day = self._days.get(symbol.upper())
            if day is not None:
                out[day.symbol] = day.snapshot(self._prev_closes.get(day.symbol))
        return out

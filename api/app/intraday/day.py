"""A stock's trading day so far, built print by print: the regular session's open, high, low,
last and volume; pre-market volume; and one-minute bars as each minute completes."""

from dataclasses import dataclass, field
from datetime import date, datetime

from app.intraday.session import Phase, minute_start, phase, session_date
from app.providers.base import MinuteBar, Snapshot, Trade


@dataclass(slots=True)
class _BarBuilder:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    def build(self, symbol: str) -> MinuteBar:
        return MinuteBar(symbol, self.ts, self.open, self.high, self.low, self.close, self.volume)


@dataclass(slots=True)
class DayState:
    symbol: str
    session: date | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    last: float | None = None
    last_ts: datetime | None = None
    # The last price before the latest print (crossing rules compare the two).
    previous: float | None = None
    volume: int = 0
    premarket_volume: int = 0
    _bar: _BarBuilder | None = field(default=None, repr=False)

    def add(self, trade: Trade) -> list[MinuteBar]:
        """Take one print; returns the minute bars it completed (the previous minute's, or the
        previous session's last). After-hours and overnight prints are ignored."""
        when = phase(trade.timestamp)
        if when not in (Phase.PREMARKET, Phase.REGULAR):
            return []
        completed: list[MinuteBar] = []
        day = session_date(trade.timestamp)
        if self.session != day:
            if (bar := self.flush()) is not None:
                completed.append(bar)
            self._reset(day)
        minute = minute_start(trade.timestamp)
        late = self.last_ts is not None and trade.timestamp < self.last_ts
        if self._bar is not None and minute > self._bar.ts:
            completed.append(self._bar.build(self.symbol))
            self._bar = None
        if self._bar is None:
            self._bar = _BarBuilder(minute, trade.price, trade.price, trade.price, trade.price, 0)
        self._bar.volume += trade.size
        if when is Phase.PREMARKET:
            self.premarket_volume += trade.size
        else:
            self.volume += trade.size
        if late:
            # A late print counts towards volume; its price is stale, so it moves nothing.
            return completed
        self._bar.high = max(self._bar.high, trade.price)
        self._bar.low = min(self._bar.low, trade.price)
        self._bar.close = trade.price
        if when is Phase.REGULAR:
            self.open = self.open if self.open is not None else trade.price
            self.high = trade.price if self.high is None else max(self.high, trade.price)
            self.low = trade.price if self.low is None else min(self.low, trade.price)
        self.previous = self.last
        self.last = trade.price
        self.last_ts = trade.timestamp
        return completed

    def flush(self) -> MinuteBar | None:
        """The minute bar still being built (at the end of a session or a replay)."""
        bar, self._bar = self._bar, None
        return None if bar is None else bar.build(self.symbol)

    def snapshot(self, prev_close: float | None) -> Snapshot:
        return Snapshot(
            symbol=self.symbol,
            ts=self.last_ts,
            last=self.last,
            prev_close=prev_close,
            open=self.open,
            high=self.high,
            low=self.low,
            volume=self.volume,
            premarket_volume=self.premarket_volume,
        )

    def _reset(self, day: date) -> None:
        self.session = day
        self.open = self.high = self.low = None
        self.last = self.previous = None
        self.last_ts = None
        self.volume = self.premarket_volume = 0

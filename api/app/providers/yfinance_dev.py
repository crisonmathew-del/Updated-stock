"""Yahoo Finance via yfinance. DEVELOPMENT ONLY (spec §5.2): unofficial, can break or throttle
without notice, and Yahoo's terms don't allow production use. Select a real provider with
PRICE_PROVIDER before deploying.

Prices are split-adjusted (Yahoo's `Close`, not dividend-adjusted `Adj Close`); volume is
split-adjusted too. Splits and dividends come back as corporate actions.
"""

import asyncio
import math
import os
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, ClassVar

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import (
    YFPricesMissingError,
    YFRateLimitError,
    YFTickerMissingError,
    YFTzMissingError,
)

from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.providers.base import (
    ActionKind,
    Bar,
    CorporateActionRecord,
    FetchResult,
    PriceHistory,
    PriceProvider,
    RateLimitedError,
)

log = get_logger(__name__)

REQUESTS_PER_SECOND = 4
CONCURRENCY = 4
RATE_LIMIT_BACKOFF_SECONDS = (30, 90, 270)
PRICE_DECIMALS = 6

_NO_DATA_ERRORS = (YFPricesMissingError, YFTzMissingError, YFTickerMissingError)


def to_yahoo(symbol: str) -> str:
    """BRK.B → BRK-B. Index symbols (^VIX) pass through."""
    return symbol if symbol.startswith("^") else symbol.replace(".", "-")


def _num(value: Any) -> float | None:
    """A finite float, or None for NaN/missing (yfinance pads non-trading rows with NaN)."""
    if isinstance(value, int | float | np.number) and math.isfinite(float(value)):
        return float(value)
    return None


def parse_history_frame(symbol: str, frame: pd.DataFrame) -> PriceHistory:
    """Convert a `Ticker.history(actions=True, auto_adjust=False)` frame to a PriceHistory.
    Rows without a full set of prices (non-trading placeholders) are dropped."""
    bars: list[Bar] = []
    actions: list[CorporateActionRecord] = []
    for timestamp, row in frame.iterrows():
        session = pd.Timestamp(str(timestamp)).date()
        o, h, low, c = (_num(row.get(col)) for col in ("Open", "High", "Low", "Close"))
        if o is not None and h is not None and low is not None and c is not None:
            volume = _num(row.get("Volume"))
            bars.append(
                Bar(
                    date=session,
                    open=round(o, PRICE_DECIMALS),
                    high=round(h, PRICE_DECIMALS),
                    low=round(low, PRICE_DECIMALS),
                    close=round(c, PRICE_DECIMALS),
                    volume=int(volume) if volume is not None else 0,
                )
            )
        split = _num(row.get("Stock Splits"))
        if split is not None and split > 0:
            actions.append(CorporateActionRecord(session, ActionKind.SPLIT, split))
        dividend = _num(row.get("Dividends"))
        if dividend is not None and dividend > 0:
            actions.append(CorporateActionRecord(session, ActionKind.DIVIDEND, dividend))
    return PriceHistory(symbol=symbol, bars=bars, actions=actions)


def _fetch_one(symbol: str, start: date, end: date) -> pd.DataFrame:
    frame: pd.DataFrame = yf.Ticker(to_yahoo(symbol)).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # Yahoo's end is exclusive
        interval="1d",
        actions=True,
        auto_adjust=False,
        raise_errors=True,
        timeout=30,
    )
    return frame


class YFinanceDevProvider(PriceProvider):
    name: ClassVar[str] = "yfinance"

    def __init__(self) -> None:
        # yfinance keeps a timezone cache on disk; keep it somewhere every user can write.
        yf.set_tz_cache_location(os.path.join(os.environ.get("TMPDIR", "/tmp"), "yfinance"))
        self._limiter = RateLimiter(get_redis(), "yahoo", per_second=REQUESTS_PER_SECOND)
        self._semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _history(self, symbol: str, start: date, end: date) -> PriceHistory:
        for attempt, backoff in enumerate((*RATE_LIMIT_BACKOFF_SECONDS, None)):
            await self._limiter.acquire()
            try:
                frame = await asyncio.to_thread(_fetch_one, symbol, start, end)
            except YFRateLimitError:
                if backoff is None:
                    raise RateLimitedError(
                        "Yahoo is still rate-limiting after backing off"
                    ) from None
                log.warning("yahoo.rate_limited", symbol=symbol, attempt=attempt + 1, wait=backoff)
                await asyncio.sleep(backoff)
                continue
            return parse_history_frame(symbol, frame)
        raise AssertionError("unreachable")

    async def daily_history(self, symbols: Sequence[str], start: date, end: date) -> FetchResult:
        histories: dict[str, PriceHistory] = {}
        errors: dict[str, str] = {}

        async def one(symbol: str) -> None:
            async with self._semaphore:
                try:
                    history = await self._history(symbol, start, end)
                except _NO_DATA_ERRORS:
                    errors[symbol] = "no data (possibly delisted or not on Yahoo)"
                    return
                except RateLimitedError:
                    raise
                except Exception as exc:
                    errors[symbol] = f"{type(exc).__name__}: {exc}"
                    return
            if history.bars:
                histories[symbol] = history
            else:
                errors[symbol] = "no bars in range"

        await asyncio.gather(*(one(s) for s in symbols))
        return FetchResult(histories=histories, errors=errors)

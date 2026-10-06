"""Phase 6: one-minute bars for the names the intraday watcher follows, and the time-of-day
volume curve learned from them."""

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IntradayBar(Base):
    """A one-minute bar (regular session and pre-market) for a watched name, kept 30 days (a
    TimescaleDB retention policy). Written by the streamer as each minute completes; replay
    reads a stored session back. `ts` is the minute's start, in UTC."""

    __tablename__ = "intraday_bars"

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))


class VolumeProfile(Base):
    """The time-of-day volume curve (spec §6.8): for each minute of a regular session, the
    median share of the day's volume traded by then (391 points, 0 at the open, 1 at the close).
    Learned from stored minute bars; the newest row is used once it covers enough sessions."""

    __tablename__ = "volume_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sessions: Mapped[int] = mapped_column(Integer)
    symbols: Mapped[int] = mapped_column(Integer)
    cumulative: Mapped[list[Any]] = mapped_column(JSONB)

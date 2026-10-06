"""Phase 6: the user's alert rules, every alert raised (with its delivery per channel) and the
holdings tracker."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AlertRule(Base):
    """ "When [a ticker / any name in a watchlist / any holding / a saved screen] [condition]
    [value], notify via [channels]" (spec §7.3). Screen rules fire for stocks that newly match."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    scope: Mapped[str] = mapped_column(String(16))  # ticker | watchlist | holdings | screen
    ticker_id: Mapped[int | None] = mapped_column(ForeignKey("tickers.id", ondelete="CASCADE"))
    watchlist_id: Mapped[int | None] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE")
    )
    screen_id: Mapped[int | None] = mapped_column(
        ForeignKey("saved_screens.id", ondelete="CASCADE")
    )
    condition: Mapped[str] = mapped_column(String(24))
    value: Mapped[float | None] = mapped_column(Float)
    ma: Mapped[str | None] = mapped_column(String(8))
    channels: Mapped[list[Any]] = mapped_column(JSONB)
    priority: Mapped[str] = mapped_column(String(8), default="normal", server_default="normal")
    last_fired_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Holding(Base):
    """A position entered by hand (no broker connection, spec §8.6). R is measured against
    the initial stop; `stop` can be raised as the trade works."""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id", ondelete="CASCADE"))
    setup_id: Mapped[int | None] = mapped_column(ForeignKey("setups.id", ondelete="SET NULL"))
    opened_on: Mapped[dt.date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Float)
    shares: Mapped[int] = mapped_column(Integer)
    initial_stop: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    closed_on: Mapped[dt.date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Alert(Base):
    """Every alert raised, once (spec §7.2-7.3): what happened, the numbers at that moment and
    how each channel handled it (`delivery`: in_app / email → sent, queued, digest, held for
    quiet hours, off, failed: …). Only `read_at`, `delivery` and `digested_at` change later."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_user_created", "user_id", text("created_at DESC")),
        Index("ix_alerts_dedupe", "user_id", "dedupe_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    session_date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(8))
    ticker_id: Mapped[int | None] = mapped_column(ForeignKey("tickers.id", ondelete="CASCADE"))
    symbol: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"))
    holding_id: Mapped[int | None] = mapped_column(ForeignKey("holdings.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str] = mapped_column(String(160))
    delivery: Mapped[dict[str, Any]] = mapped_column(JSONB)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    digested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

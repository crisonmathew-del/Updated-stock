"""Phase 5: the user's own things: watchlists (ordered, with a note per stock), saved screener
screens and a free-text note per stock. Everything is keyed by user so multi-user works later."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "ticker_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    note: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SavedScreen(Base):
    """A screener screen: filters on computed fields, the sort and the visible columns."""

    __tablename__ = "saved_screens"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    filters: Mapped[list[Any]] = mapped_column(JSONB)
    sort: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    columns: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StockNote(Base):
    __tablename__ = "stock_notes"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

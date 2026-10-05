from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TickerType(StrEnum):
    COMMON = "common"
    ADR = "adr"
    ETF = "etf"
    INDEX = "index"


class BackfillStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    NO_DATA = "no_data"
    FAILED = "failed"


class Ticker(Base):
    """A listed security. Symbols can be reused after a delisting, so uniqueness is enforced only
    among active rows; inactive rows are kept forever to avoid survivorship bias."""

    __tablename__ = "tickers"
    __table_args__ = (
        Index("uq_tickers_active_symbol", "symbol", unique=True, postgresql_where=text("active")),
        Index("ix_tickers_cik", "cik"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(16))
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Reference data from SEC EDGAR. Sector/industry/group are derived in Phase 2.
    cik: Mapped[str | None] = mapped_column(String(10))
    sic_code: Mapped[str | None] = mapped_column(String(4))
    sic_description: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(160))
    industry_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("industry_groups.id", ondelete="SET NULL")
    )
    reference_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Latest values, refreshed by the EOD update (history lives in daily_bars/shares_outstanding).
    market_cap: Mapped[float | None] = mapped_column(Float)
    float_shares: Mapped[float | None] = mapped_column(Float)

    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    listed_date: Mapped[date | None] = mapped_column(Date)
    delisted_date: Mapped[date | None] = mapped_column(Date)

    backfill_status: Mapped[str] = mapped_column(
        String(16), default=BackfillStatus.PENDING, server_default=BackfillStatus.PENDING
    )
    backfill_error: Mapped[str | None] = mapped_column(Text)
    backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bars_start: Mapped[date | None] = mapped_column(Date)
    bars_end: Mapped[date | None] = mapped_column(Date)
    # Set whenever a ticker's bars are (re)written by a backfill; the analytics pipeline then
    # recomputes its full indicator history and clears the flag.
    indicators_stale: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

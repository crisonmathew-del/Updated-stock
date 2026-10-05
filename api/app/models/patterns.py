"""Phase 3 pattern detections and the owner's review verdicts.

A base keeps one row from its first detection while it forms (keyed by ticker, type,
timeframe and start date); each day's detection updates it. Events (pocket pivots, earnings
gaps) are one-day rows. JSON columns hold what the chart and the explanation need: the swing
points, the contractions and the quality score's components (see app.patterns).
"""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Pattern(Base):
    __tablename__ = "patterns"
    __table_args__ = (
        UniqueConstraint("ticker_id", "type", "timeframe", "start_date"),
        Index("ix_patterns_last_seen", "last_seen"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    pivot: Mapped[float] = mapped_column(Float)
    base_low: Mapped[float | None] = mapped_column(Float)
    depth_pct: Mapped[float | None] = mapped_column(Float)
    duration_weeks: Mapped[float] = mapped_column(Float)
    quality: Mapped[float] = mapped_column(Float)
    base_number: Mapped[int | None] = mapped_column(SmallInteger)
    # forming | broken_out | failed | expired
    status: Mapped[str] = mapped_column(String(16))
    status_date: Mapped[dt.date] = mapped_column(Date)
    components: Mapped[list[Any]] = mapped_column(JSONB)
    swings: Mapped[list[Any]] = mapped_column(JSONB)
    contractions: Mapped[list[Any]] = mapped_column(JSONB)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    first_detected: Mapped[dt.date] = mapped_column(Date)
    last_seen: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PatternReview(Base):
    """The owner's verdict on a detection (spec §15 Phase 3: false-positive review)."""

    __tablename__ = "pattern_reviews"

    pattern_id: Mapped[int] = mapped_column(
        ForeignKey("patterns.id", ondelete="CASCADE"), primary_key=True
    )
    verdict: Mapped[str] = mapped_column(String(16))  # correct | wrong | unsure
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

"""Phase 3 fundamentals: point-in-time financial statements, earnings dates, insider trades and
the daily Fundamentals Grade.

Statements are versioned: one row per fiscal period per filing date that changed any figure
(`reported_date`). A calculation for date D uses, per period, the latest row with
reported_date <= D. See app.providers.sec_facts for how versions are built.
"""

import datetime as dt
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class _StatementColumns:
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    period_end: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    reported_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    period_start: Mapped[dt.date] = mapped_column(Date)
    fiscal_year: Mapped[int | None] = mapped_column(SmallInteger)
    fiscal_period: Mapped[str | None] = mapped_column(String(4))
    form: Mapped[str | None] = mapped_column(String(16))
    accession: Mapped[str | None] = mapped_column(String(25))
    currency: Mapped[str | None] = mapped_column(String(3))
    eps_diluted: Mapped[float | None] = mapped_column(Float)
    eps_basic: Mapped[float | None] = mapped_column(Float)
    revenue: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    operating_income: Mapped[float | None] = mapped_column(Float)
    equity: Mapped[float | None] = mapped_column(Float)
    derived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(16))


class FundamentalsQuarterly(_StatementColumns, Base):
    """Quarterly figures. `derived` marks a quarter computed from year-to-date totals (usually
    Q4 = full year - nine months), which is approximate for EPS."""

    __tablename__ = "fundamentals_quarterly"


class FundamentalsAnnual(_StatementColumns, Base):
    __tablename__ = "fundamentals_annual"


class EarningsEvent(Base):
    """A results release (8-K item 2.02) or the next estimated one. `timing` says when the
    release reached the market: before_open, during_session, after_close or unknown."""

    __tablename__ = "earnings_calendar"

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    report_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # reported | estimated
    timing: Mapped[str] = mapped_column(String(16))
    accession: Mapped[str | None] = mapped_column(String(25))
    source: Mapped[str] = mapped_column(String(16))


class InsiderTransaction(Base):
    """Open-market purchases (P) and sales (S) from Form 4. One row per transaction per listed
    share class of the issuer."""

    __tablename__ = "insider_transactions"
    __table_args__ = (
        Index("ix_insider_transactions_ticker_date", "ticker_id", "transaction_date"),
    )

    accession: Mapped[str] = mapped_column(String(25), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    filed_date: Mapped[dt.date] = mapped_column(Date)
    transaction_date: Mapped[dt.date] = mapped_column(Date)
    insider_cik: Mapped[str] = mapped_column(String(10))
    insider_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(160))
    is_director: Mapped[bool] = mapped_column(Boolean)
    is_officer: Mapped[bool] = mapped_column(Boolean)
    is_ten_percent_owner: Mapped[bool] = mapped_column(Boolean)
    code: Mapped[str] = mapped_column(String(1))
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))


class FundamentalGrade(Base):
    """The Fundamentals Grade as of a session. `components` lists every rule with its points
    and the numbers behind it (see app.fundamentals.grade)."""

    __tablename__ = "fundamental_grades"
    __table_args__ = (Index("ix_fundamental_grades_date", "date"),)

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    grade: Mapped[str | None] = mapped_column(String(2))
    score: Mapped[float | None] = mapped_column(Float)
    path: Mapped[str] = mapped_column(String(16))  # eps | revenue
    basis: Mapped[str] = mapped_column(String(16))  # quarterly | annual | none
    coverage_pct: Mapped[float] = mapped_column(Float)
    components: Mapped[list[Any]] = mapped_column(JSONB)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

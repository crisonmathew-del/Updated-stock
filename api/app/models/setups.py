"""Phase 4: setups (one active per stock, moving through the lifecycle), their stage history,
the immutable signal log and each signal's tracked outcome.

See app.scoring.setup_score (the score), app.scoring.lifecycle (the stages),
app.risk.trade_plan (entry, stop, size) and app.scanner.setups (the EOD stage).
"""

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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Setup(Base):
    """A stock's setup: a base (or an earnings gap) moving from basing to breakout, or a trend
    leader on watch. `active` is True for at most one setup per stock; a failed, invalidated
    or superseded setup is closed (kept for history)."""

    __tablename__ = "setups"
    __table_args__ = (
        Index("uq_setups_active_ticker", "ticker_id", unique=True, postgresql_where=text("active")),
        Index("ix_setups_state", "state", "active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id", ondelete="CASCADE"))
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("patterns.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))  # watch | base | episodic_pivot
    pattern_type: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    state_since: Mapped[dt.date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    first_seen: Mapped[dt.date] = mapped_column(Date)
    as_of: Mapped[dt.date] = mapped_column(Date)  # the session last evaluated
    close: Mapped[float] = mapped_column(Float)
    pivot: Mapped[float | None] = mapped_column(Float)
    base_low: Mapped[float | None] = mapped_column(Float)
    readiness_pct: Mapped[float | None] = mapped_column(Float)  # % below the pivot (< 0: above)
    score: Mapped[float] = mapped_column(Float)  # final, after regime and penalties
    raw_score: Mapped[float] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(2))  # A+ | A | B | C | None
    regime_multiplier: Mapped[float] = mapped_column(Float)
    penalties: Mapped[float] = mapped_column(Float)
    components: Mapped[list[Any]] = mapped_column(JSONB)
    red_flags: Mapped[list[Any]] = mapped_column(JSONB)
    trade_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    breakout_date: Mapped[dt.date | None] = mapped_column(Date)
    best_grade: Mapped[str | None] = mapped_column(String(2))
    closed_on: Mapped[dt.date | None] = mapped_column(Date)
    closed_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SetupTransition(Base):
    __tablename__ = "setup_transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    setup_id: Mapped[int] = mapped_column(ForeignKey("setups.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Signal(Base):
    """An alert-worthy event, logged once with a snapshot of everything known at that moment.
    Never updated: re-running a session inserts nothing new (unique per date, type, stock)."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("date", "type", "ticker_id", postgresql_nulls_not_distinct=True),
        Index("ix_signals_ticker_date", "ticker_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    type: Mapped[str] = mapped_column(String(32))
    ticker_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE")
    )  # None for market-wide signals (regime changes)
    setup_id: Mapped[int | None] = mapped_column(ForeignKey("setups.id", ondelete="SET NULL"))
    summary: Mapped[str] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)  # the session's close
    pivot: Mapped[float | None] = mapped_column(Float)
    entry: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(2))
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SignalOutcome(Base):
    """What happened after a signal: % change from the signal day's close after 1, 5, 10, 20
    and 60 sessions, the best and worst move so far (MFE/MAE, by highs and lows), and when the
    stop, the 2R level and +20% were first reached (when the signal had a plan)."""

    __tablename__ = "signal_outcomes"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    sessions_observed: Mapped[int] = mapped_column(Integer)
    ret_1: Mapped[float | None] = mapped_column(Float)
    ret_5: Mapped[float | None] = mapped_column(Float)
    ret_10: Mapped[float | None] = mapped_column(Float)
    ret_20: Mapped[float | None] = mapped_column(Float)
    ret_60: Mapped[float | None] = mapped_column(Float)
    mfe_pct: Mapped[float | None] = mapped_column(Float)
    mae_pct: Mapped[float | None] = mapped_column(Float)
    stop_hit_on: Mapped[dt.date | None] = mapped_column(Date)
    target_2r_on: Mapped[dt.date | None] = mapped_column(Date)
    gain_20_on: Mapped[dt.date | None] = mapped_column(Date)
    complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

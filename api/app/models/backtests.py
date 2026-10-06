"""Phase 7: the backtest lab. A tape is stage 1's cached output (Parquet files under
BACKTEST_DIR, rebuilt on demand); a run is one portfolio simulation on a tape with its report.
Runs belong to a user; tapes are shared (they depend only on the dates and the settings)."""

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BacktestTape(Base):
    __tablename__ = "backtest_tapes"
    __table_args__ = (Index("ix_backtest_tapes_lookup", "settings_hash", "start", "end"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start: Mapped[dt.date] = mapped_column(Date)
    end: Mapped[dt.date] = mapped_column(Date)
    settings_hash: Mapped[str] = mapped_column(String(64))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cells: Mapped[list[Any]] = mapped_column(JSONB)  # [{"vcp": 10, "volume": 140}, ...]
    status: Mapped[str] = mapped_column(String(16))  # running | done | failed
    path: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16))  # queued | running | done | failed
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tape_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_tapes.id", ondelete="SET NULL")
    )
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # headline metrics
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trades: Mapped[list[Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

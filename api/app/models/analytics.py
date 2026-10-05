"""Phase 2 analytics tables: daily indicators, industry groups and ranks, market regime and
breadth. Column meanings are documented in the modules that compute them (app.indicators,
app.groups, app.market)."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IndicatorDaily(Base):
    """One row per ticker per session. A TimescaleDB hypertable; older chunks are compressed."""

    __tablename__ = "indicators_daily"

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    ema10: Mapped[float | None] = mapped_column(Float)
    ema21: Mapped[float | None] = mapped_column(Float)
    sma50: Mapped[float | None] = mapped_column(Float)
    sma150: Mapped[float | None] = mapped_column(Float)
    sma200: Mapped[float | None] = mapped_column(Float)
    sma50_slope: Mapped[float | None] = mapped_column(Float)
    sma150_slope: Mapped[float | None] = mapped_column(Float)
    sma200_slope: Mapped[float | None] = mapped_column(Float)
    atr14: Mapped[float | None] = mapped_column(Float)
    atr_ratio_10_50: Mapped[float | None] = mapped_column(Float)
    bb_width: Mapped[float | None] = mapped_column(Float)
    high_52w: Mapped[float | None] = mapped_column(Float)
    low_52w: Mapped[float | None] = mapped_column(Float)
    close_high_52w: Mapped[float | None] = mapped_column(Float)
    history_sessions: Mapped[int | None] = mapped_column(Integer)
    avg_volume_50: Mapped[float | None] = mapped_column(Float)
    avg_dollar_volume_50: Mapped[float | None] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    up_down_volume_50: Mapped[float | None] = mapped_column(Float)
    roc_63: Mapped[float | None] = mapped_column(Float)
    roc_126: Mapped[float | None] = mapped_column(Float)
    roc_189: Mapped[float | None] = mapped_column(Float)
    roc_252: Mapped[float | None] = mapped_column(Float)
    rs_raw: Mapped[float | None] = mapped_column(Float)
    rs_rating: Mapped[int | None] = mapped_column(SmallInteger)
    rs_line: Mapped[float | None] = mapped_column(Float)
    rs_line_high_52w: Mapped[bool | None] = mapped_column(Boolean)
    rs_new_high_ahead: Mapped[bool | None] = mapped_column(Boolean)
    rs_slope_21: Mapped[float | None] = mapped_column(Float)
    rs_slope_63: Mapped[float | None] = mapped_column(Float)
    stage: Mapped[int | None] = mapped_column(SmallInteger)


class IndustryGroup(Base):
    __tablename__ = "industry_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    sector: Mapped[str] = mapped_column(String(48))
    sic_level: Mapped[int] = mapped_column(SmallInteger)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GroupRankDaily(Base):
    __tablename__ = "group_rank_history"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("industry_groups.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    rank: Mapped[int] = mapped_column(SmallInteger)
    score: Mapped[float] = mapped_column(Float)
    members: Mapped[int] = mapped_column(Integer)
    median_rs: Mapped[float | None] = mapped_column(Float)
    return_3m: Mapped[float | None] = mapped_column(Float)
    return_6m: Mapped[float | None] = mapped_column(Float)
    tt_passing: Mapped[int] = mapped_column(Integer)
    new_highs: Mapped[int] = mapped_column(Integer)
    rank_change_4w: Mapped[int | None] = mapped_column(SmallInteger)


class MarketBreadthDaily(Base):
    __tablename__ = "market_breadth_daily"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    members: Mapped[int] = mapped_column(Integer)
    with_50: Mapped[int] = mapped_column(Integer)
    above_50: Mapped[int] = mapped_column(Integer)
    with_200: Mapped[int] = mapped_column(Integer)
    above_200: Mapped[int] = mapped_column(Integer)
    new_highs: Mapped[int] = mapped_column(Integer)
    new_lows: Mapped[int] = mapped_column(Integer)
    advancers: Mapped[int] = mapped_column(Integer)
    decliners: Mapped[int] = mapped_column(Integer)
    pct_above_50: Mapped[float | None] = mapped_column(Float)
    pct_above_200: Mapped[float | None] = mapped_column(Float)
    net_new_highs: Mapped[int] = mapped_column(Integer)
    ad_line: Mapped[float] = mapped_column(Float)


class MarketRegimeDaily(Base):
    """One row per index per session, plus index_symbol='MARKET' for the overall state."""

    __tablename__ = "market_regime_daily"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    index_symbol: Mapped[str] = mapped_column(String(8), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    close: Mapped[float | None] = mapped_column(Float)
    ema21: Mapped[float | None] = mapped_column(Float)
    sma50: Mapped[float | None] = mapped_column(Float)
    sma200: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    is_distribution_day: Mapped[bool] = mapped_column(Boolean)
    distribution_days: Mapped[int] = mapped_column(Integer)
    distribution_dates: Mapped[list[str]] = mapped_column(JSONB)
    rally_day: Mapped[int | None] = mapped_column(Integer)
    rally_low: Mapped[float | None] = mapped_column(Float)
    is_ftd: Mapped[bool] = mapped_column(Boolean)
    last_ftd_date: Mapped[dt.date | None] = mapped_column(Date)
    pct_above_50: Mapped[float | None] = mapped_column(Float)
    changed_from: Mapped[str | None] = mapped_column(String(32))
    reasons: Mapped[list[Any]] = mapped_column(JSONB)

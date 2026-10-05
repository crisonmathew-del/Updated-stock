from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DailyBar(Base):
    """Split-adjusted daily OHLCV. A TimescaleDB hypertable partitioned on `date` (see the
    migration). Prices are adjusted as of the latest split; use `corporate_actions` to recover
    as-traded prices for a historical date."""

    __tablename__ = "daily_bars"

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger)
    vwap: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))


class CorporateAction(Base):
    """Splits (`value` = new shares per old share, e.g. 4.0 for 4-for-1) and cash dividends
    (`value` = amount per share)."""

    __tablename__ = "corporate_actions"

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharesOutstanding(Base):
    """Shares outstanding as reported on a filing's cover page. `filed_date` is when the number
    became public, which is what point-in-time calculations must key on."""

    __tablename__ = "shares_outstanding"

    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), primary_key=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    filed_date: Mapped[date] = mapped_column(Date, primary_key=True)
    shares: Mapped[int] = mapped_column(BigInteger)
    form: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))

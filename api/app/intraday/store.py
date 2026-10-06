"""Minute bars in the database: saved as the streamer completes them (that is the recording),
read back for replay, export and the intraday chart; and the time-of-day volume curve learned
from them."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import polars as pl
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import MARKET_TZ
from app.core.logging import get_logger
from app.data.loaders import read_frame
from app.intraday.session import session_times
from app.intraday.volume import STANDARD_CURVE, VolumeCurve, learn_curve
from app.models import DailyBar, IntradayBar, Ticker, VolumeProfile
from app.providers.base import MinuteBar
from app.settings.schema import AppSettings

log = get_logger(__name__)

LEARN_LOOKBACK_DAYS = 30  # as long as minute bars are kept


async def ticker_ids(session: AsyncSession, symbols: Sequence[str]) -> dict[str, int]:
    rows = await session.execute(
        select(Ticker.symbol, Ticker.id).where(Ticker.symbol.in_({s.upper() for s in symbols}))
    )
    return {symbol: int(tid) for symbol, tid in rows.all()}


async def save_bars(
    session: AsyncSession, bars: Sequence[MinuteBar], ids: dict[str, int], source: str
) -> int:
    """Store minute bars (a minute already stored is kept). Returns how many were known."""
    rows = [
        {
            "ticker_id": ids[b.symbol],
            "ts": b.ts,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "source": source,
        }
        for b in bars
        if b.symbol in ids
    ]
    if rows:
        await session.execute(
            insert(IntradayBar)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["ticker_id", "ts"])
        )
        await session.commit()
    return len(rows)


async def load_bars(
    session: AsyncSession, day: date, symbols: Sequence[str] | None = None
) -> list[MinuteBar]:
    """One session's stored minute bars (pre-market included), oldest first."""
    start = datetime.combine(day, datetime.min.time(), MARKET_TZ)
    query = (
        select(Ticker.symbol, IntradayBar)
        .join(Ticker, Ticker.id == IntradayBar.ticker_id)
        .where(IntradayBar.ts >= start, IntradayBar.ts < start + timedelta(days=1))
        .order_by(IntradayBar.ts, Ticker.symbol)
    )
    if symbols is not None:
        query = query.where(Ticker.symbol.in_({s.upper() for s in symbols}))
    rows = await session.execute(query)
    return [
        MinuteBar(symbol, b.ts, b.open, b.high, b.low, b.close, int(b.volume))
        for symbol, b in rows.all()
    ]


async def prev_closes(session: AsyncSession, day: date, symbols: Sequence[str]) -> dict[str, float]:
    """Each symbol's last daily close before `day`."""
    latest = (
        select(DailyBar.ticker_id, func.max(DailyBar.date).label("d"))
        .where(DailyBar.date < day)
        .group_by(DailyBar.ticker_id)
        .subquery()
    )
    rows = await session.execute(
        select(Ticker.symbol, DailyBar.close)
        .join(latest, latest.c.ticker_id == Ticker.id)
        .join(DailyBar, (DailyBar.ticker_id == latest.c.ticker_id) & (DailyBar.date == latest.c.d))
        .where(Ticker.symbol.in_({s.upper() for s in symbols}))
    )
    return {symbol: float(close) for symbol, close in rows.all()}


async def current_curve(session: AsyncSession, settings: AppSettings) -> VolumeCurve:
    """The learned curve when it covers enough sessions, else the standard one."""
    profile = await session.scalar(select(VolumeProfile).order_by(VolumeProfile.id.desc()).limit(1))
    if profile is None or profile.sessions < settings.volume_curve_min_sessions:
        return STANDARD_CURVE
    return VolumeCurve(
        tuple(float(v) for v in profile.cumulative), f"learned from {profile.sessions} sessions"
    )


async def learn_volume_curve(
    session: AsyncSession, settings: AppSettings, today: date
) -> VolumeCurve | None:
    """Learn the curve from the stored regular-session minutes of the last 30 days; stores and
    returns it when there are enough complete sessions."""
    since = today - timedelta(days=LEARN_LOOKBACK_DAYS)
    frame = await read_frame(
        "SELECT ticker_id, ts, volume FROM intraday_bars "
        f"WHERE ts >= '{since.isoformat()}' AND ts < '{(today + timedelta(days=1)).isoformat()}'"
    )
    if frame.is_empty():
        return None
    local = frame.with_columns(pl.col("ts").dt.convert_time_zone(str(MARKET_TZ)).alias("local"))
    local = local.with_columns(pl.col("local").dt.date().alias("session"))
    opens = {
        d: t.open
        for d in local["session"].unique().to_list()
        if (t := session_times(d)) is not None and t.minutes == 390
    }
    if not opens:
        return None
    minutes = (
        local.filter(pl.col("session").is_in(list(opens)))
        .with_columns(
            (
                (
                    pl.col("local").dt.hour().cast(pl.Int32) * 60
                    + pl.col("local").dt.minute().cast(pl.Int32)
                )
                - (9 * 60 + 30)
            ).alias("minute")
        )
        .filter((pl.col("minute") >= 0) & (pl.col("minute") < 390))
    )
    curve = learn_curve(
        minutes.select("ticker_id", "session", "minute", "volume"),
        settings.volume_curve_min_sessions,
    )
    if curve is None:
        return None
    sessions = int(minutes["session"].n_unique())
    session.add(
        VolumeProfile(
            sessions=sessions,
            symbols=int(minutes["ticker_id"].n_unique()),
            cumulative=list(curve.cumulative),
        )
    )
    await session.commit()
    log.info("intraday.volume_curve_learned", sessions=sessions)
    return curve

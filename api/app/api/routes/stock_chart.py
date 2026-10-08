"""Everything the stock page's chart draws, in one request (spec §8.3, §10: two years of daily
data in one response).

- series: candles, volume and its average, the moving averages and the RS line, as columnar
  arrays (daily: 10/21-day EMA, 50/150/200-day SMA; weekly: 10/30/40-week SMA).
- markers: pocket pivots, earnings releases, earnings gaps, the first day of each RS line new
  high ahead of price, and past signals.
- overlay: the base the stock's setup follows (or its latest detected base): swing points
  for the outline, numbered contractions with their depth, pivot, base low, buy zone, and the
  plan's entry, stop, 2R and 3R when there is a setup.
Weekly bars are labelled with the week's first session; markers and overlay dates are moved
to the week they fall in.
"""

from datetime import date, timedelta
from typing import Any, Literal

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbSession, current_user
from app.data.loaders import query_frame
from app.models import Pattern, Setup, Signal, Ticker
from app.patterns.types import EVENTS, LABELS, PatternType
from app.scanner.evaluate import SIGNAL_LABELS
from app.settings import store

router = APIRouter(prefix="/stocks", tags=["stocks"], dependencies=[Depends(current_user)])

Timeframe = Literal["daily", "weekly"]
DAILY_MAS = ("ema10", "ema21", "sma50", "sma150", "sma200")
WEEKLY_MAS = {"sma10w": 10, "sma30w": 30, "sma40w": 40}
WEEKLY_AVG_VOLUME = 10
BASE_TYPES = tuple(str(t) for t in PatternType if t not in EVENTS)


class ChartSeries(BaseModel):
    time: list[str]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]
    avg_volume: list[float | None]
    ma: dict[str, list[float | None]]
    rs_line: list[float | None]


class ChartMarker(BaseModel):
    time: str
    kind: str  # pocket_pivot | earnings | gap | rs_high | signal
    label: str  # the short text drawn on the chart
    text: str  # the explanation shown on hover


class OverlayPoint(BaseModel):
    time: str
    price: float
    kind: str  # high | low


class OverlayContraction(BaseModel):
    number: int
    high: OverlayPoint
    low: OverlayPoint
    depth_pct: float


class ChartOverlay(BaseModel):
    pattern_id: int
    type: str
    label: str
    status: str
    start: str
    end: str
    pivot: float
    base_low: float | None
    buy_zone_top: float
    swings: list[OverlayPoint]
    contractions: list[OverlayContraction]
    setup_state: str | None
    entry: float | None
    stop: float | None
    target_2r: float | None
    target_3r: float | None


class ChartOut(BaseModel):
    symbol: str
    timeframe: Timeframe
    series: ChartSeries
    markers: list[ChartMarker]
    overlay: ChartOverlay | None


def _round(values: list[Any], digits: int = 4) -> list[Any]:
    return [None if v is None else round(float(v), digits) for v in values]


def week_label(dates: list[date]) -> dict[date, date]:
    """Each session → the first session of its ISO week (present in `dates`)."""
    first: dict[tuple[int, int], date] = {}
    for d in dates:
        key = d.isocalendar()[:2]
        first.setdefault(key, d)
    return {d: first[d.isocalendar()[:2]] for d in dates}


def weekly(frame: pl.DataFrame) -> pl.DataFrame:
    """Daily rows (sorted) → weekly candles labelled by the week's first session, with the
    weekly SMAs, 10-week average volume and the week's last RS line value."""
    labels = week_label(frame["date"].to_list())
    weeks = (
        frame.with_columns(pl.Series("week", [labels[d] for d in frame["date"].to_list()]))
        .group_by("week", maintain_order=True)
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
            pl.col("rs_line").last(),
        )
        .rename({"week": "date"})
    )
    return weeks.with_columns(
        *(pl.col("close").rolling_mean(n).alias(name) for name, n in WEEKLY_MAS.items()),
        pl.col("volume").rolling_mean(WEEKLY_AVG_VOLUME).shift(1).alias("avg_volume_50"),
    )


async def _ticker(db: DbSession, symbol: str) -> Ticker:
    ticker = await db.scalar(
        select(Ticker)
        .where(Ticker.symbol == symbol.upper())
        .order_by(Ticker.active.desc(), Ticker.id.desc())
        .limit(1)
    )
    if ticker is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No ticker {symbol.upper()} in the universe."
        )
    return ticker


async def _markers(
    db: DbSession, ticker_id: int, first: date, last: date, place: dict[date, date]
) -> list[ChartMarker]:
    def at(day: date) -> str | None:
        mapped = place.get(day)
        return None if mapped is None else mapped.isoformat()

    out: list[ChartMarker] = []
    events = await query_frame(
        db,
        "SELECT type, start_date, details, quality FROM patterns "
        f"WHERE ticker_id = {int(ticker_id)} AND type IN ('pocket_pivot', 'earnings_gap') "
        f"AND start_date BETWEEN '{first.isoformat()}' AND '{last.isoformat()}'",
    )
    for kind, day, _, quality in events.iter_rows():
        time = at(day)
        if time is None:
            continue
        if kind == "pocket_pivot":
            out.append(
                ChartMarker(
                    time=time,
                    kind="pocket_pivot",
                    label="PP",
                    text=f"Pocket pivot (quality {quality:.0f}/100)",
                )
            )
        else:
            out.append(
                ChartMarker(
                    time=time,
                    kind="gap",
                    label="G",
                    text=f"Earnings gap (quality {quality:.0f}/100)",
                )
            )
    releases = await query_frame(
        db,
        "SELECT report_date, timing FROM earnings_calendar "
        f"WHERE ticker_id = {int(ticker_id)} AND status = 'reported' "
        f"AND report_date BETWEEN '{first.isoformat()}' AND '{last.isoformat()}'",
    )
    for day, timing in releases.iter_rows():
        time = at(day) or at(_next_known(day, place))
        if time is not None:
            when = str(timing).replace("_", " ")
            out.append(
                ChartMarker(
                    time=time,
                    kind="earnings",
                    label="E",
                    text=f"Results released {day.isoformat()} ({when})",
                )
            )
    highs = await query_frame(
        db,
        "SELECT date, rs_new_high_ahead FROM indicators_daily "
        f"WHERE ticker_id = {int(ticker_id)} AND date BETWEEN '{first.isoformat()}' "
        f"AND '{last.isoformat()}' ORDER BY date",
    )
    previous = False
    for day, ahead in highs.iter_rows():
        now = bool(ahead)
        time = at(day)
        if now and not previous and time is not None:
            out.append(
                ChartMarker(
                    time=time,
                    kind="rs_high",
                    label="RS",
                    text="RS line at a new 52-week high ahead of price",
                )
            )
        previous = now
    signals = await db.execute(
        select(Signal.date, Signal.type, Signal.summary)
        .where(Signal.ticker_id == ticker_id, Signal.date.between(first, last))
        .order_by(Signal.date)
    )
    for day, kind, summary in signals.all():
        time = at(day)
        if time is not None and kind not in ("pocket_pivot", "earnings_gap"):
            out.append(
                ChartMarker(
                    time=time,
                    kind="signal",
                    label="◆",
                    text=f"{SIGNAL_LABELS.get(kind, kind)}: {summary}",
                )
            )
    out.sort(key=lambda m: m.time)
    return out


def _next_known(day: date, place: dict[date, date]) -> date:
    """A release dated on a non-session (weekend filing) shows on the next session."""
    for step in range(1, 5):
        candidate = day + timedelta(days=step)
        if candidate in place:
            return candidate
    return day


def _point(raw: dict[str, Any], place: dict[date, date]) -> OverlayPoint | None:
    day = date.fromisoformat(raw["date"])
    mapped = place.get(day)
    if mapped is None:
        return None
    return OverlayPoint(time=mapped.isoformat(), price=float(raw["price"]), kind=raw["kind"])


async def _overlay(
    db: DbSession, ticker_id: int, place: dict[date, date], buy_zone_pct: float
) -> ChartOverlay | None:
    setup = await db.scalar(select(Setup).where(Setup.ticker_id == ticker_id, Setup.active))
    pattern = None
    if setup is not None and setup.pattern_id is not None:
        pattern = await db.get(Pattern, setup.pattern_id)
    if pattern is None:
        pattern = await db.scalar(
            select(Pattern)
            .where(Pattern.ticker_id == ticker_id, Pattern.type.in_(BASE_TYPES))
            .order_by(Pattern.last_seen.desc(), Pattern.quality.desc())
            .limit(1)
        )
    if pattern is None:
        return None
    swings = [p for p in (_point(s, place) for s in pattern.swings) if p is not None]
    contractions = []
    for n, c in enumerate(pattern.contractions, start=1):
        high, low = _point(c["high"], place), _point(c["low"], place)
        if high is not None and low is not None:
            contractions.append(
                OverlayContraction(number=n, high=high, low=low, depth_pct=float(c["depth_pct"]))
            )
    start, end = place.get(pattern.start_date), place.get(pattern.end_date)
    if start is None or end is None:
        return None
    plan = (setup.trade_plan or {}) if setup is not None and setup.pattern_id == pattern.id else {}
    pivot = float(setup.pivot) if setup is not None and setup.pivot and plan else pattern.pivot
    try:
        label = LABELS[PatternType(pattern.type)]
    except ValueError:
        label = pattern.type
    return ChartOverlay(
        pattern_id=pattern.id,
        type=pattern.type,
        label=label,
        status=pattern.status,
        start=start.isoformat(),
        end=end.isoformat(),
        pivot=round(pivot, 4),
        base_low=pattern.base_low,
        buy_zone_top=round(pivot * (1 + buy_zone_pct / 100), 4),
        swings=swings,
        contractions=contractions,
        setup_state=setup.state if setup is not None and plan else None,
        entry=plan.get("entry"),
        stop=plan.get("stop"),
        target_2r=plan.get("target_2r"),
        target_3r=plan.get("target_3r"),
    )


@router.get("/{symbol}/chart", response_model=ChartOut)
async def stock_chart(
    db: DbSession,
    symbol: str,
    timeframe: Timeframe = "daily",
    sessions: int = Query(504, ge=20, le=2520, description="Daily sessions to return"),
) -> ChartOut:
    ticker = await _ticker(db, symbol)
    settings = await store.load(db)
    # Weekly needs 40 extra weeks for its slowest average.
    load = sessions if timeframe == "daily" else sessions + 40 * 5
    frame = (
        await query_frame(
            db,
            "SELECT b.date, b.open, b.high, b.low, b.close, b.volume, i.ema10, i.ema21, "
            "i.sma50, i.sma150, i.sma200, i.avg_volume_50, i.rs_line FROM daily_bars b "
            "LEFT JOIN indicators_daily i ON i.ticker_id = b.ticker_id AND i.date = b.date "
            f"WHERE b.ticker_id = {int(ticker.id)} ORDER BY b.date DESC LIMIT {int(load)}",
        )
    ).sort("date")
    if frame.is_empty():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No price history for {ticker.symbol} yet. Prices load by themselves after the "
            "stock list is built (progress on Admin → Data; make backfill loads them by hand).",
        )
    days = frame["date"].to_list()
    if timeframe == "weekly":
        bars = weekly(frame)
        keep = max(1, sessions // 5)
        bars = bars.tail(keep)
        place = {d: w for d, w in week_label(days).items() if w >= bars["date"][0]}
        ma_columns = list(WEEKLY_MAS)
    else:
        bars = frame
        place = {d: d for d in days}
        ma_columns = list(DAILY_MAS)
    series = ChartSeries(
        time=[d.isoformat() for d in bars["date"].to_list()],
        open=_round(bars["open"].to_list()),
        high=_round(bars["high"].to_list()),
        low=_round(bars["low"].to_list()),
        close=_round(bars["close"].to_list()),
        volume=[float(v) for v in bars["volume"].to_list()],
        avg_volume=_round(bars["avg_volume_50"].to_list(), 0),
        ma={c: _round(bars[c].to_list()) for c in ma_columns},
        rs_line=_round(bars["rs_line"].to_list(), 6),
    )
    first, last = min(place), max(place)
    return ChartOut(
        symbol=ticker.symbol,
        timeframe=timeframe,
        series=series,
        markers=await _markers(db, ticker.id, first, last, place),
        overlay=await _overlay(db, ticker.id, place, settings.buy_zone_max_pct_above_pivot),
    )

"""Per-stock analytics: latest indicators, Trend Template checklist and history."""

import datetime as dt
from datetime import date, datetime
from functools import partial
from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession, current_user
from app.api.routes.patterns import PatternOut, pattern_query, to_out
from app.core.calendar import sessions_between
from app.data.loaders import query_frame
from app.fundamentals.grade import YEAR_AGO, StatementRow, as_known, find_period, growth
from app.fundamentals.scan import load_grade_inputs
from app.indicators.compute import INDICATOR_COLUMNS
from app.models import (
    EarningsEvent,
    FundamentalGrade,
    GroupRankDaily,
    IndicatorDaily,
    IndustryGroup,
    InsiderTransaction,
    Pattern,
    StockNote,
    Ticker,
    Watchlist,
    WatchlistItem,
)
from app.scoring.trend_template import add_trend_template, explain_trend_template
from app.settings import store

router = APIRouter(prefix="/stocks", tags=["stocks"], dependencies=[Depends(current_user)])

STAGE_LABELS = {
    1: "Stage 1 · basing",
    2: "Stage 2 · advancing",
    3: "Stage 3 · topping",
    4: "Stage 4 · declining",
}


class CheckOut(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str


class GroupOut(BaseModel):
    id: int
    name: str
    sector: str
    rank: int | None
    rank_change_4w: int | None
    ranked_groups: int | None


class StockSummary(BaseModel):
    symbol: str
    name: str
    exchange: str
    type: str
    sector: str | None
    industry: str | None
    market_cap: float | None
    date: date | None
    close: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    volume: float | None
    volume_ratio: float | None  # the session's volume ÷ the 50-day average
    high_52w: float | None
    low_52w: float | None
    next_earnings: dt.date | None  # reported calendar or estimated from last year
    sessions_to_earnings: int | None
    fundamentals_grade: str | None
    stage: int | None
    stage_label: str | None
    rs_rating: int | None
    trend_template_passed: int
    trend_template_pass: bool
    checks: list[CheckOut]
    group: GroupOut | None
    indicators: dict[str, Any]


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


async def _indicator_frame(
    db: DbSession, ticker_id: int, through: date | None, sessions: int
) -> pl.DataFrame:
    cutoff = f"AND i.date <= '{through}'" if through else ""
    frame = await query_frame(
        db,
        "SELECT i.*, b.close, b.high, b.low, b.volume FROM indicators_daily i "
        "JOIN daily_bars b ON b.ticker_id = i.ticker_id AND b.date = i.date "
        f"WHERE i.ticker_id = {int(ticker_id)} {cutoff} ORDER BY i.date DESC LIMIT {int(sessions)}",
    )
    return frame.sort("date")


@router.get("/{symbol}", response_model=StockSummary)
async def stock_summary(db: DbSession, symbol: str, on: date | None = None) -> StockSummary:
    ticker = await _ticker(db, symbol)
    settings = await store.load(db)
    frame = await _indicator_frame(db, ticker.id, on, settings.ma200_uptrend_lookback_days + 1)
    row: dict[str, Any] | None = None
    checks: list[CheckOut] = []
    if not frame.is_empty():
        evaluated = add_trend_template(frame, settings)
        row = evaluated.row(-1, named=True)
        checks = [CheckOut(**vars(c)) for c in explain_trend_template(row, settings)]

    group: GroupOut | None = None
    if ticker.industry_group_id is not None:
        g = await db.get(IndustryGroup, ticker.industry_group_id)
        latest_rank_date = await db.scalar(select(func.max(GroupRankDaily.date)))
        rank = (
            await db.get(GroupRankDaily, (ticker.industry_group_id, latest_rank_date))
            if latest_rank_date
            else None
        )
        ranked = (
            await db.scalar(
                select(func.count())
                .select_from(GroupRankDaily)
                .where(GroupRankDaily.date == latest_rank_date)
            )
            if latest_rank_date
            else None
        )
        if g is not None:
            group = GroupOut(
                id=g.id,
                name=g.name,
                sector=g.sector,
                rank=rank.rank if rank else None,
                rank_change_4w=rank.rank_change_4w if rank else None,
                ranked_groups=ranked,
            )

    stage = row["stage"] if row else None
    close = row["close"] if row else None
    prev_close = float(frame["close"][-2]) if frame.height >= 2 else None
    change = None if close is None or prev_close is None else close - prev_close
    as_of = row["date"] if row else None
    upcoming = await db.scalar(
        select(func.min(EarningsEvent.report_date)).where(
            EarningsEvent.ticker_id == ticker.id,
            EarningsEvent.report_date > (as_of or date.today()),
        )
    )
    grade = await db.scalar(
        select(FundamentalGrade.grade)
        .where(FundamentalGrade.ticker_id == ticker.id)
        .order_by(FundamentalGrade.date.desc())
        .limit(1)
    )
    return StockSummary(
        symbol=ticker.symbol,
        name=ticker.name,
        exchange=ticker.exchange,
        type=ticker.type,
        sector=ticker.sector,
        industry=ticker.industry,
        market_cap=ticker.market_cap,
        date=as_of,
        close=close,
        prev_close=prev_close,
        change=None if change is None else round(change, 4),
        change_pct=None
        if change is None or not prev_close
        else round(change / prev_close * 100, 2),
        volume=row["volume"] if row else None,
        volume_ratio=row["volume_ratio"] if row else None,
        high_52w=row["high_52w"] if row else None,
        low_52w=row["low_52w"] if row else None,
        next_earnings=upcoming,
        sessions_to_earnings=(
            len(sessions_between(as_of, upcoming)) - 1 if upcoming and as_of else None
        ),
        fundamentals_grade=grade,
        stage=stage,
        stage_label=STAGE_LABELS.get(stage) if stage else None,
        rs_rating=row["rs_rating"] if row else None,
        trend_template_passed=int(row["tt_passed"]) if row else 0,
        trend_template_pass=bool(row["tt_pass"]) if row else False,
        checks=checks,
        group=group,
        indicators={k: row[k] for k in INDICATOR_COLUMNS} if row else {},
    )


@router.get("/{symbol}/indicators")
async def stock_indicators(
    db: DbSession, symbol: str, days: int = Query(260, ge=1, le=5000)
) -> list[dict[str, Any]]:
    ticker = await _ticker(db, symbol)
    frame = await _indicator_frame(db, ticker.id, None, days)
    return [
        {
            k: (v.isoformat() if isinstance(v, date) else v)
            for k, v in row.items()
            if k != "ticker_id"
        }
        for row in frame.iter_rows(named=True)
    ]


# --- Fundamentals (Phase 3) -----------------------------------------------------------------


class GradeComponentOut(BaseModel):
    key: str
    label: str
    points: float
    max_points: float
    status: str
    detail: str
    bonus: bool


class GradeOut(BaseModel):
    date: date
    grade: str | None
    score: float | None
    path: str
    basis: str
    coverage_pct: float
    components: list[GradeComponentOut]


class PeriodOut(BaseModel):
    period_end: date
    label: str
    reported_date: date
    eps: float | None
    revenue: float | None
    net_income: float | None
    eps_growth_pct: float | None
    eps_note: str | None  # turnaround | loss when growth isn't meaningful
    revenue_growth_pct: float | None
    derived: bool
    currency: str | None


class EarningsOut(BaseModel):
    report_date: date
    status: str
    timing: str


class InsiderOut(BaseModel):
    transaction_date: date
    filed_date: date
    insider_name: str
    role: str
    code: str
    shares: float
    price: float | None


class FundamentalsOut(BaseModel):
    symbol: str
    as_of: date | None
    refreshed_at: datetime | None
    grade: GradeOut | None
    quarters: list[PeriodOut]
    years: list[PeriodOut]
    earnings: list[EarningsOut]
    insiders: list[InsiderOut]


def _periods(rows: list[StatementRow], annual: bool, keep: int) -> list[PeriodOut]:
    out = []
    for row in rows[-keep:][::-1]:
        prior = find_period(rows, row.period_end, YEAR_AGO)
        eps = growth(row.eps, prior.eps if prior else None)
        sales = growth(row.revenue, prior.revenue if prior else None)
        if row.fiscal_year is not None and row.fiscal_period is not None:
            label = f"FY{row.fiscal_year}" if annual else f"{row.fiscal_period} FY{row.fiscal_year}"
        else:
            label = row.period_end.isoformat()
        out.append(
            PeriodOut(
                period_end=row.period_end,
                label=label,
                reported_date=row.reported_date,
                eps=row.eps,
                revenue=row.revenue,
                net_income=row.net_income,
                eps_growth_pct=None if eps.value is None else round(eps.value, 1),
                eps_note=eps.kind if eps.kind in ("turnaround", "loss") else None,
                revenue_growth_pct=None if sales.value is None else round(sales.value, 1),
                derived=row.derived,
                currency=row.currency,
            )
        )
    return out


@router.get("/{symbol}/fundamentals", response_model=FundamentalsOut)
async def stock_fundamentals(db: DbSession, symbol: str, on: date | None = None) -> FundamentalsOut:
    """The grade with every component, recent quarters and years as they were known on `on`
    (default: the latest session with analytics), the earnings calendar and insider trades."""
    ticker = await _ticker(db, symbol)
    settings = await store.load(db)
    as_of = await db.scalar(
        select(func.max(IndicatorDaily.date)).where(
            IndicatorDaily.ticker_id == ticker.id,
            *([IndicatorDaily.date <= on] if on else []),
        )
    )
    grade: GradeOut | None = None
    quarters: list[PeriodOut] = []
    years: list[PeriodOut] = []
    if as_of is not None:
        inputs = (
            await load_grade_inputs([ticker.id], as_of, settings, read=partial(query_frame, db))
        )[ticker.id]
        result = inputs.grade(as_of, settings)
        grade = GradeOut(
            date=as_of,
            grade=result.grade,
            score=result.score,
            path=result.path,
            basis=result.basis,
            coverage_pct=result.coverage_pct,
            components=[GradeComponentOut(**c) for c in result.components_json()],
        )
        quarters = _periods(as_known(inputs.quarterly, as_of, inputs.splits), False, 12)
        years = _periods(as_known(inputs.annual, as_of, inputs.splits), True, 6)
    calendar = (
        await db.execute(
            select(EarningsEvent)
            .where(EarningsEvent.ticker_id == ticker.id)
            .order_by(EarningsEvent.report_date.desc())
            .limit(9)
        )
    ).scalars()
    trades = (
        await db.execute(
            select(InsiderTransaction)
            .where(
                InsiderTransaction.ticker_id == ticker.id,
                *([InsiderTransaction.filed_date <= on] if on else []),
            )
            .order_by(InsiderTransaction.transaction_date.desc(), InsiderTransaction.seq)
            .limit(25)
        )
    ).scalars()
    return FundamentalsOut(
        symbol=ticker.symbol,
        as_of=as_of,
        refreshed_at=ticker.fundamentals_refreshed_at,
        grade=grade,
        quarters=quarters,
        years=years,
        earnings=[
            EarningsOut(report_date=e.report_date, status=e.status, timing=e.timing)
            for e in calendar
        ],
        insiders=[
            InsiderOut(
                transaction_date=t.transaction_date,
                filed_date=t.filed_date,
                insider_name=t.insider_name,
                role=t.role,
                code=t.code,
                shares=t.shares,
                price=t.price,
            )
            for t in trades
        ],
    )


@router.get("/{symbol}/patterns", response_model=list[PatternOut])
async def stock_patterns(
    db: DbSession, symbol: str, limit: int = Query(20, ge=1, le=200)
) -> list[PatternOut]:
    """Detections for this stock, most recently seen first."""
    ticker = await _ticker(db, symbol)
    rows = (
        await db.execute(
            pattern_query()
            .where(Pattern.ticker_id == ticker.id)
            .order_by(Pattern.last_seen.desc(), Pattern.quality.desc())
            .limit(limit)
        )
    ).all()
    return [to_out(*row) for row in rows]


# --- Peers, notes, watchlist membership (Phase 5) --------------------------------------------


class PeerOut(BaseModel):
    symbol: str
    name: str
    close: float | None
    change_pct: float | None
    rs_rating: int | None
    stage: int | None
    grade: str | None
    score: float | None
    state: str | None
    is_self: bool


@router.get("/{symbol}/peers", response_model=list[PeerOut])
async def stock_peers(
    db: DbSession, symbol: str, limit: int = Query(12, ge=1, le=50)
) -> list[PeerOut]:
    """Stocks in the same industry group on the latest session, strongest first (setup score,
    then RS Rating). Includes the stock itself so its place in the group shows."""
    ticker = await _ticker(db, symbol)
    if ticker.industry_group_id is None:
        return []
    latest = await db.scalar(select(func.max(IndicatorDaily.date)))
    if latest is None:
        return []
    frame = await query_frame(
        db,
        "SELECT t.id, t.symbol, t.name, b.close, p.close AS prev_close, i.rs_rating, i.stage, "
        "s.grade, s.score, s.state FROM tickers t "
        "JOIN indicators_daily i ON i.ticker_id = t.id "
        f"AND i.date = '{latest.isoformat()}' "
        "JOIN daily_bars b ON b.ticker_id = t.id AND b.date = i.date "
        "LEFT JOIN LATERAL (SELECT close FROM daily_bars x WHERE x.ticker_id = t.id "
        "AND x.date < i.date ORDER BY x.date DESC LIMIT 1) p ON true "
        "LEFT JOIN setups s ON s.ticker_id = t.id AND s.active "
        f"WHERE t.active AND NOT t.is_benchmark AND t.industry_group_id = "
        f"{int(ticker.industry_group_id)} "
        "ORDER BY s.score DESC NULLS LAST, i.rs_rating DESC NULLS LAST, t.symbol "
        f"LIMIT {int(limit)}",
    )
    out = []
    for r in frame.iter_rows(named=True):
        prev = r["prev_close"]
        out.append(
            PeerOut(
                symbol=r["symbol"],
                name=r["name"],
                close=r["close"],
                change_pct=round((r["close"] / prev - 1) * 100, 2) if prev else None,
                rs_rating=r["rs_rating"],
                stage=r["stage"],
                grade=r["grade"],
                score=r["score"],
                state=r["state"],
                is_self=r["id"] == ticker.id,
            )
        )
    return out


class NoteOut(BaseModel):
    body: str
    updated_at: datetime | None


class NoteIn(BaseModel):
    body: str = Field(max_length=20_000)


@router.get("/{symbol}/note", response_model=NoteOut)
async def get_note(db: DbSession, user: AuthUser, symbol: str) -> NoteOut:
    ticker = await _ticker(db, symbol)
    note = await db.get(StockNote, (user.id, ticker.id))
    return NoteOut(body=note.body if note else "", updated_at=note.updated_at if note else None)


@router.put("/{symbol}/note", response_model=NoteOut)
async def put_note(db: DbSession, user: AuthUser, symbol: str, payload: NoteIn) -> NoteOut:
    """Save the note (an empty body deletes it)."""
    ticker = await _ticker(db, symbol)
    note = await db.get(StockNote, (user.id, ticker.id))
    if not payload.body.strip():
        if note is not None:
            await db.delete(note)
            await db.commit()
        return NoteOut(body="", updated_at=None)
    if note is None:
        note = StockNote(user_id=user.id, ticker_id=ticker.id, body=payload.body)
        db.add(note)
    else:
        note.body = payload.body
    await db.commit()
    await db.refresh(note)
    return NoteOut(body=note.body, updated_at=note.updated_at)


class MembershipOut(BaseModel):
    id: int
    name: str
    contains: bool


@router.get("/{symbol}/watchlists", response_model=list[MembershipOut])
async def stock_watchlists(db: DbSession, user: AuthUser, symbol: str) -> list[MembershipOut]:
    """The user's watchlists and whether each holds this stock (for the ☆ menu)."""
    ticker = await _ticker(db, symbol)
    rows = await db.execute(
        select(Watchlist.id, Watchlist.name, WatchlistItem.id)
        .outerjoin(
            WatchlistItem,
            (WatchlistItem.watchlist_id == Watchlist.id) & (WatchlistItem.ticker_id == ticker.id),
        )
        .where(Watchlist.user_id == user.id)
        .order_by(Watchlist.position, Watchlist.id)
    )
    return [MembershipOut(id=i, name=n, contains=item is not None) for i, n, item in rows.all()]

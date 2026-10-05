"""Per-stock analytics: latest indicators, Trend Template checklist and history."""

from datetime import date, datetime
from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DbSession, current_user
from app.api.routes.patterns import PatternOut, pattern_query, to_out
from app.data.loaders import read_frame
from app.fundamentals.grade import YEAR_AGO, StatementRow, as_known, find_period, growth
from app.fundamentals.scan import load_grade_inputs
from app.indicators.compute import INDICATOR_COLUMNS
from app.models import (
    EarningsEvent,
    GroupRankDaily,
    IndicatorDaily,
    IndustryGroup,
    InsiderTransaction,
    Pattern,
    Ticker,
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


async def _indicator_frame(ticker_id: int, through: date | None, sessions: int) -> pl.DataFrame:
    cutoff = f"AND i.date <= '{through}'" if through else ""
    frame = await read_frame(
        "SELECT i.*, b.close, b.high, b.low, b.volume FROM indicators_daily i "
        "JOIN daily_bars b ON b.ticker_id = i.ticker_id AND b.date = i.date "
        f"WHERE i.ticker_id = {int(ticker_id)} {cutoff} ORDER BY i.date DESC LIMIT {int(sessions)}"
    )
    return frame.sort("date")


@router.get("/{symbol}", response_model=StockSummary)
async def stock_summary(db: DbSession, symbol: str, on: date | None = None) -> StockSummary:
    ticker = await _ticker(db, symbol)
    settings = await store.load(db)
    frame = await _indicator_frame(ticker.id, on, settings.ma200_uptrend_lookback_days + 1)
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
    return StockSummary(
        symbol=ticker.symbol,
        name=ticker.name,
        exchange=ticker.exchange,
        type=ticker.type,
        sector=ticker.sector,
        industry=ticker.industry,
        market_cap=ticker.market_cap,
        date=row["date"] if row else None,
        close=row["close"] if row else None,
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
    frame = await _indicator_frame(ticker.id, None, days)
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
        inputs = (await load_grade_inputs([ticker.id], as_of, settings))[ticker.id]
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

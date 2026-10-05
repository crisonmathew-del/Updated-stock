"""Per-stock analytics: latest indicators, Trend Template checklist and history."""

from datetime import date
from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DbSession, current_user
from app.data.loaders import read_frame
from app.indicators.compute import INDICATOR_COLUMNS
from app.models import GroupRankDaily, IndustryGroup, Ticker
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

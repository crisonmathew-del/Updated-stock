"""Market regime, breadth, industry groups and sector rotation (spec §6.1, §6.6)."""

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text

from app.api.deps import DbSession, current_user
from app.groups.classification import SECTOR_ETFS
from app.groups.industry_rank import RANK_TREND_SESSIONS
from app.intraday.live import INDEX_SYMBOLS
from app.market.regime import LABELS, MARKET, RegimeState
from app.models import (
    GroupRankDaily,
    IndicatorDaily,
    IndustryGroup,
    MarketBreadthDaily,
    MarketRegimeDaily,
    Ticker,
)

router = APIRouter(tags=["market"], dependencies=[Depends(current_user)])


class IndexRegime(BaseModel):
    symbol: str
    state: str
    label: str
    close: float | None
    ema21: float | None
    sma50: float | None
    sma200: float | None
    change_pct: float | None
    distribution_days: int
    distribution_dates: list[str]
    rally_day: int | None
    is_ftd: bool
    last_ftd_date: date | None
    reasons: list[str]


class RegimeHistoryDay(BaseModel):
    date: date
    states: dict[str, str]
    distribution_days: dict[str, int]
    is_ftd: bool


class RegimeResponse(BaseModel):
    date: date | None
    state: str | None
    label: str | None
    changed_from: str | None
    reasons: list[str]
    indexes: list[IndexRegime]
    history: list[RegimeHistoryDay]


class BreadthDay(BaseModel):
    date: date
    members: int
    pct_above_50: float | None
    pct_above_200: float | None
    new_highs: int
    new_lows: int
    net_new_highs: int
    advancers: int
    decliners: int
    ad_line: float


class GroupRow(BaseModel):
    group_id: int
    rank: int
    rank_change_4w: int | None
    name: str
    sector: str
    members: int
    median_rs: float | None
    return_3m: float | None
    return_6m: float | None
    tt_passing: int
    new_highs: int


class SectorRow(BaseModel):
    symbol: str
    sector: str
    rank: int
    rank_change_4w: int | None
    rs_raw: float | None
    return_3m: float | None


class GroupsResponse(BaseModel):
    date: date | None
    groups: list[GroupRow]
    sectors: list[SectorRow]


def _reasons(raw: Any) -> list[str]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return [str(r) for r in (value or [])]


@router.get("/market/regime", response_model=RegimeResponse)
async def market_regime(db: DbSession, days: int = Query(60, ge=1, le=2000)) -> RegimeResponse:
    latest = await db.scalar(select(func.max(MarketRegimeDaily.date)))
    if latest is None:
        return RegimeResponse(
            date=None, state=None, label=None, changed_from=None, reasons=[], indexes=[], history=[]
        )
    rows = (
        await db.scalars(select(MarketRegimeDaily).where(MarketRegimeDaily.date == latest))
    ).all()
    market = next((r for r in rows if r.index_symbol == MARKET), None)
    indexes = [
        IndexRegime(
            symbol=r.index_symbol,
            state=r.state,
            label=LABELS[RegimeState(r.state)],
            close=r.close,
            ema21=r.ema21,
            sma50=r.sma50,
            sma200=r.sma200,
            change_pct=r.change_pct,
            distribution_days=r.distribution_days,
            distribution_dates=[str(d) for d in r.distribution_dates or []],
            rally_day=r.rally_day,
            is_ftd=r.is_ftd,
            last_ftd_date=r.last_ftd_date,
            reasons=_reasons(r.reasons),
        )
        for r in sorted(rows, key=lambda r: r.index_symbol)
        if r.index_symbol != MARKET
    ]
    recent_dates = (
        await db.scalars(
            select(MarketRegimeDaily.date)
            .where(MarketRegimeDaily.index_symbol == MARKET)
            .order_by(MarketRegimeDaily.date.desc())
            .limit(days)
        )
    ).all()
    history_rows = (
        await db.scalars(select(MarketRegimeDaily).where(MarketRegimeDaily.date.in_(recent_dates)))
    ).all()
    by_date: dict[date, RegimeHistoryDay] = {}
    for r in history_rows:
        day = by_date.setdefault(
            r.date, RegimeHistoryDay(date=r.date, states={}, distribution_days={}, is_ftd=False)
        )
        day.states[r.index_symbol] = r.state
        day.distribution_days[r.index_symbol] = r.distribution_days
        day.is_ftd = day.is_ftd or r.is_ftd
    return RegimeResponse(
        date=latest,
        state=market.state if market else None,
        label=LABELS[RegimeState(market.state)] if market else None,
        changed_from=market.changed_from if market else None,
        reasons=_reasons(market.reasons) if market else [],
        indexes=indexes,
        history=[by_date[d] for d in sorted(by_date, reverse=True)],
    )


@router.get("/market/breadth", response_model=list[BreadthDay])
async def market_breadth(db: DbSession, days: int = Query(60, ge=1, le=2000)) -> list[BreadthDay]:
    rows = (
        await db.scalars(
            select(MarketBreadthDaily).order_by(MarketBreadthDaily.date.desc()).limit(days)
        )
    ).all()
    return [BreadthDay.model_validate(r, from_attributes=True) for r in rows]


@router.get("/groups", response_model=GroupsResponse)
async def industry_groups(db: DbSession, limit: int = Query(50, ge=1, le=500)) -> GroupsResponse:
    latest = await db.scalar(select(func.max(GroupRankDaily.date)))
    groups: list[GroupRow] = []
    if latest is not None:
        rows = await db.execute(
            select(GroupRankDaily, IndustryGroup)
            .join(IndustryGroup, IndustryGroup.id == GroupRankDaily.group_id)
            .where(GroupRankDaily.date == latest)
            .order_by(GroupRankDaily.rank)
            .limit(limit)
        )
        groups = [
            GroupRow(
                group_id=g.id,
                rank=r.rank,
                rank_change_4w=r.rank_change_4w,
                name=g.name,
                sector=g.sector,
                members=r.members,
                median_rs=r.median_rs,
                return_3m=r.return_3m,
                return_6m=r.return_6m,
                tt_passing=r.tt_passing,
                new_highs=r.new_highs,
            )
            for r, g in rows.all()
        ]
    return GroupsResponse(date=latest, groups=groups, sectors=await _sector_rotation(db))


async def _sector_rotation(db: DbSession) -> list[SectorRow]:
    ids = dict(
        (
            await db.execute(
                select(Ticker.id, Ticker.symbol).where(
                    Ticker.symbol.in_(SECTOR_ETFS), Ticker.is_benchmark
                )
            )
        ).all()
    )
    if not ids:
        return []
    dates = (
        await db.scalars(
            select(IndicatorDaily.date)
            .where(IndicatorDaily.ticker_id.in_(ids))
            .distinct()
            .order_by(IndicatorDaily.date.desc())
            .limit(RANK_TREND_SESSIONS + 1)
        )
    ).all()
    if not dates:
        return []
    latest, earlier = dates[0], dates[-1] if len(dates) > RANK_TREND_SESSIONS else None

    async def ranks(day: date) -> dict[int, tuple[int, float | None, float | None]]:
        rows = (
            await db.execute(
                select(IndicatorDaily.ticker_id, IndicatorDaily.rs_raw, IndicatorDaily.roc_63)
                .where(IndicatorDaily.ticker_id.in_(ids), IndicatorDaily.date == day)
                .order_by(IndicatorDaily.rs_raw.desc().nulls_last())
            )
        ).all()
        return {tid: (i + 1, raw, roc) for i, (tid, raw, roc) in enumerate(rows)}

    now = await ranks(latest)
    before = await ranks(earlier) if earlier else {}
    return [
        SectorRow(
            symbol=ids[tid],
            sector=SECTOR_ETFS[ids[tid]],
            rank=rank,
            rank_change_4w=(before[tid][0] - rank) if tid in before else None,
            rs_raw=raw,
            return_3m=roc,
        )
        for tid, (rank, raw, roc) in sorted(now.items(), key=lambda kv: kv[1][0])
    ]


# --- Index quotes for the top bar (Phase 5) ---------------------------------------------------

QUOTE_SYMBOLS = INDEX_SYMBOLS


class QuoteOut(BaseModel):
    symbol: str
    date: date | None
    close: float | None
    change_pct: float | None


@router.get("/market/quotes", response_model=list[QuoteOut])
async def market_quotes(db: DbSession) -> list[QuoteOut]:
    """The latest close and % change of SPY, QQQ and IWM (the page overlays live quotes)."""
    rows = await db.execute(
        text(
            "SELECT t.symbol, lb.date, lb.close, lb.prev_close FROM tickers t "
            "LEFT JOIN LATERAL (SELECT b.date, b.close, lag(b.close) OVER (ORDER BY b.date) "
            "AS prev_close FROM (SELECT date, close FROM daily_bars x WHERE x.ticker_id = t.id "
            "ORDER BY x.date DESC LIMIT 2) b ORDER BY b.date DESC LIMIT 1) lb ON true "
            "WHERE t.symbol = ANY(:symbols) AND t.is_benchmark"
        ),
        {"symbols": list(QUOTE_SYMBOLS)},
    )
    found = {
        symbol: QuoteOut(
            symbol=symbol,
            date=day,
            close=close,
            change_pct=round((close / prev - 1) * 100, 2) if close and prev else None,
        )
        for symbol, day, close, prev in rows.all()
    }
    return [
        found.get(s, QuoteOut(symbol=s, date=None, close=None, change_pct=None))
        for s in QUOTE_SYMBOLS
    ]

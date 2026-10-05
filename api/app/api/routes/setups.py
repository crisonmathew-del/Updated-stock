"""Setups and the signal log (Phase 4). Everything shown comes with its numbers: the score's
components, the red flags, the trade plan, the stage history and each signal's outcome.

- GET /setups                  active setups (filter by stage, grade, kind; sort by score,
                               readiness or recency), or closed ones with active=false
- GET /setups/{id}             one setup with its breakdown, plan, history, signals, pattern
- GET /stocks/{symbol}/setup   a stock's current setup (or its latest closed one)
- GET /signals                 the signal log with outcomes (filter by type, stock, date)
"""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Select, func, select

from app.api.deps import DbSession, current_user
from app.api.routes.patterns import PatternOut, pattern_query, to_out
from app.models import (
    Pattern,
    ScanProgress,
    Setup,
    SetupTransition,
    Signal,
    SignalOutcome,
    Ticker,
)
from app.patterns.types import LABELS as PATTERN_LABELS
from app.patterns.types import PatternType
from app.scanner.daily import STAGE
from app.scanner.evaluate import SIGNAL_LABELS
from app.scanner.outcomes import HORIZONS
from app.scoring.lifecycle import LABELS as STATE_LABELS
from app.scoring.lifecycle import State

router = APIRouter(tags=["setups"], dependencies=[Depends(current_user)])

SortKey = Literal["score", "readiness", "recent", "symbol"]
# R needs a trade taken at the plan's entry: true for a confirmed breakout (the close is at the
# entry). Other signals fire before the entry is reached, so their R would be hypothetical.
R_SIGNALS = frozenset({"breakout"})


class SetupRow(BaseModel):
    id: int
    symbol: str
    name: str
    kind: str
    pattern_type: str | None
    pattern_label: str | None
    state: str
    state_label: str
    state_since: date
    first_seen: date
    as_of: date
    active: bool
    close: float
    pivot: float | None
    base_low: float | None
    readiness_pct: float | None
    score: float
    raw_score: float
    grade: str | None
    best_grade: str | None
    breakout_date: date | None
    entry: float | None
    stop: float | None
    shares: int | None
    risk_too_wide: bool | None
    red_flags: list[str]
    closed_on: date | None
    closed_reason: str | None


class TransitionOut(BaseModel):
    date: date
    from_state: str | None
    to_state: str
    to_label: str
    reason: str


class OutcomeOut(BaseModel):
    sessions_observed: int
    returns: dict[str, float | None]  # % change of the close after N sessions
    returns_r: dict[str, float | None]  # the same in R, for breakouts (entry and stop)
    mfe_pct: float | None
    mae_pct: float | None
    stop_hit_on: date | None
    target_2r_on: date | None
    gain_20_on: date | None
    complete: bool


class SignalOut(BaseModel):
    id: int
    date: date
    type: str
    type_label: str
    symbol: str | None
    name: str | None
    setup_id: int | None
    summary: str
    price: float | None
    pivot: float | None
    entry: float | None
    stop: float | None
    score: float | None
    grade: str | None
    context: dict[str, Any]
    outcome: OutcomeOut | None
    setup_state: str | None  # the setup's stage now (the signal itself never changes)


class SetupDetail(SetupRow):
    regime_multiplier: float
    penalties: float
    components: list[dict[str, Any]]
    red_flag_details: list[dict[str, Any]]
    trade_plan: dict[str, Any] | None
    transitions: list[TransitionOut]
    signals: list[SignalOut]
    pattern: PatternOut | None


class SetupList(BaseModel):
    as_of: date | None  # the last session the setups stage processed
    total: int
    counts: dict[str, int]  # active setups per stage
    items: list[SetupRow]


class SignalList(BaseModel):
    total: int
    counts: dict[str, int]  # signals per type (with the same stock/date filters)
    items: list[SignalOut]


def _pattern_label(kind: str | None) -> str | None:
    if kind is None:
        return None
    try:
        return PATTERN_LABELS[PatternType(kind)]
    except ValueError:
        return kind


def _state_label(state: str) -> str:
    try:
        return STATE_LABELS[State(state)]
    except ValueError:
        return state


def _row(setup: Setup, ticker: Ticker) -> SetupRow:
    plan = setup.trade_plan or {}
    return SetupRow(
        id=setup.id,
        symbol=ticker.symbol,
        name=ticker.name,
        kind=setup.kind,
        pattern_type=setup.pattern_type,
        pattern_label=_pattern_label(setup.pattern_type),
        state=setup.state,
        state_label=_state_label(setup.state),
        state_since=setup.state_since,
        first_seen=setup.first_seen,
        as_of=setup.as_of,
        active=setup.active,
        close=setup.close,
        pivot=setup.pivot,
        base_low=setup.base_low,
        readiness_pct=setup.readiness_pct,
        score=setup.score,
        raw_score=setup.raw_score,
        grade=setup.grade,
        best_grade=setup.best_grade,
        breakout_date=setup.breakout_date,
        entry=plan.get("entry"),
        stop=plan.get("stop"),
        shares=plan.get("shares"),
        risk_too_wide=plan.get("risk_too_wide"),
        red_flags=[f["label"] for f in setup.red_flags or []],
        closed_on=setup.closed_on,
        closed_reason=setup.closed_reason,
    )


def _in_r(
    price: float | None, ret: float | None, entry: float | None, stop: float | None
) -> float | None:
    if price is None or ret is None or entry is None or stop is None or entry <= stop:
        return None
    return round((price * (1 + ret / 100) - entry) / (entry - stop), 2)


def _signal(
    signal: Signal,
    ticker: Ticker | None,
    outcome: SignalOutcome | None,
    setup_state: str | None = None,
) -> SignalOut:
    measured = None
    if outcome is not None:
        returns = {str(n): getattr(outcome, f"ret_{n}") for n in HORIZONS}
        measured = OutcomeOut(
            sessions_observed=outcome.sessions_observed,
            returns=returns,
            returns_r={
                n: _in_r(signal.price, r, signal.entry, signal.stop)
                if signal.type in R_SIGNALS
                else None
                for n, r in returns.items()
            },
            mfe_pct=outcome.mfe_pct,
            mae_pct=outcome.mae_pct,
            stop_hit_on=outcome.stop_hit_on,
            target_2r_on=outcome.target_2r_on,
            gain_20_on=outcome.gain_20_on,
            complete=outcome.complete,
        )
    return SignalOut(
        id=signal.id,
        date=signal.date,
        type=signal.type,
        type_label=SIGNAL_LABELS.get(signal.type, signal.type),
        symbol=None if ticker is None else ticker.symbol,
        name=None if ticker is None else ticker.name,
        setup_id=signal.setup_id,
        summary=signal.summary,
        price=signal.price,
        pivot=signal.pivot,
        entry=signal.entry,
        stop=signal.stop,
        score=signal.score,
        grade=signal.grade,
        context=signal.context,
        outcome=measured,
        setup_state=setup_state,
    )


def _signal_query() -> Select[Signal, Ticker, SignalOutcome, str]:
    return (
        select(Signal, Ticker, SignalOutcome, Setup.state)
        .outerjoin(Setup, Setup.id == Signal.setup_id)
        .outerjoin(Ticker, Ticker.id == Signal.ticker_id)
        .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
    )


async def _detail(db: DbSession, setup: Setup, ticker: Ticker) -> SetupDetail:
    transitions = (
        await db.scalars(
            select(SetupTransition)
            .where(SetupTransition.setup_id == setup.id)
            .order_by(SetupTransition.date, SetupTransition.id)
        )
    ).all()
    signals = (
        await db.execute(
            _signal_query().where(Signal.setup_id == setup.id).order_by(Signal.date, Signal.id)
        )
    ).all()
    pattern = None
    if setup.pattern_id is not None:
        found = (await db.execute(pattern_query().where(Pattern.id == setup.pattern_id))).first()
        if found is not None:
            pattern = to_out(*found)
    return SetupDetail(
        **_row(setup, ticker).model_dump(),
        regime_multiplier=setup.regime_multiplier,
        penalties=setup.penalties,
        components=setup.components,
        red_flag_details=setup.red_flags,
        trade_plan=setup.trade_plan,
        transitions=[
            TransitionOut(
                date=t.date,
                from_state=t.from_state,
                to_state=t.to_state,
                to_label=_state_label(t.to_state),
                reason=t.reason,
            )
            for t in transitions
        ],
        signals=[_signal(*row) for row in signals],
        pattern=pattern,
    )


@router.get("/setups", response_model=SetupList)
async def list_setups(
    db: DbSession,
    state: str | None = Query(None, description="One state, or several separated by commas"),
    grade: str | None = Query(None, description="A+, A, B, C; or 'top' for A and A+"),
    kind: str | None = None,
    active: bool = True,
    sort: SortKey = "score",
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> SetupList:
    query = select(Setup, Ticker).join(Ticker, Ticker.id == Setup.ticker_id)
    query = query.where(Setup.active.is_(active))
    if state:
        query = query.where(Setup.state.in_([s.strip() for s in state.split(",") if s.strip()]))
    if grade == "top":
        query = query.where(Setup.grade.in_(("A+", "A")))
    elif grade:
        query = query.where(Setup.grade == grade)
    if kind:
        query = query.where(Setup.kind == kind)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    orders: dict[str, tuple[Any, ...]] = {
        "score": (Setup.score.desc(), Ticker.symbol),
        "readiness": (func.abs(Setup.readiness_pct).asc().nulls_last(), Setup.score.desc()),
        "recent": (Setup.state_since.desc(), Setup.score.desc()),
        "symbol": (Ticker.symbol,),
    }
    order = orders[sort]
    if not active:
        order = (Setup.closed_on.desc().nulls_last(), *order)
    rows = (await db.execute(query.order_by(*order).limit(limit).offset(offset))).all()
    counts = dict(
        (
            await db.execute(
                select(Setup.state, func.count()).where(Setup.active).group_by(Setup.state)
            )
        ).all()
    )
    progress = await db.get(ScanProgress, STAGE)
    return SetupList(
        as_of=None if progress is None else progress.through,
        total=int(total),
        counts={str(k): int(v) for k, v in counts.items()},
        items=[_row(s, t) for s, t in rows],
    )


@router.get("/setups/{setup_id}", response_model=SetupDetail)
async def get_setup(db: DbSession, setup_id: int) -> SetupDetail:
    found = (
        await db.execute(
            select(Setup, Ticker)
            .join(Ticker, Ticker.id == Setup.ticker_id)
            .where(Setup.id == setup_id)
        )
    ).first()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No setup {setup_id}.")
    return await _detail(db, *found)


@router.get("/stocks/{symbol}/setup", response_model=SetupDetail | None)
async def stock_setup(db: DbSession, symbol: str) -> SetupDetail | None:
    found = (
        await db.execute(
            select(Setup, Ticker)
            .join(Ticker, Ticker.id == Setup.ticker_id)
            .where(Ticker.symbol == symbol.upper())
            .order_by(Setup.active.desc(), Setup.as_of.desc(), Setup.id.desc())
            .limit(1)
        )
    ).first()
    return None if found is None else await _detail(db, *found)


@router.get("/signals", response_model=SignalList)
async def list_signals(
    db: DbSession,
    kind: str | None = Query(None, alias="type"),
    symbol: str | None = None,
    since: date | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> SignalList:
    filters = []
    if symbol:
        filters.append(Ticker.symbol == symbol.upper())
    if since:
        filters.append(Signal.date >= since)
    counted = (
        select(Signal.type, func.count())
        .outerjoin(Ticker, Ticker.id == Signal.ticker_id)
        .where(*filters)
        .group_by(Signal.type)
    )
    counts = {str(k): int(v) for k, v in (await db.execute(counted)).all()}
    if kind:
        filters.append(Signal.type == kind)
    query = _signal_query().where(*filters)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await db.execute(
            query.order_by(Signal.date.desc(), Signal.type, Ticker.symbol)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return SignalList(total=int(total), counts=counts, items=[_signal(*r) for r in rows])

"""The buy list (Phase 8): what the app's rules say to do next, in one place.

- GET /buy-list   the market's verdict, orders for the next session (setups the rules say to
                  buy or get ready for, each with the order to place), the breakout watch
                  (bases most likely to break out soon, with their readiness parts and any
                  early entry), and the watch's track record (how often its picks broke out).
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, select

from app.api.deps import DbSession, current_user
from app.api.routes.setups import SetupRow, _row
from app.core.calendar import sessions_between
from app.market.regime import MARKET
from app.models import MarketRegimeDaily, ScanProgress, Setup, Signal, Ticker
from app.scanner.daily import STAGE
from app.scanner.watch_record import track_record
from app.scoring.verdict import REGIME_WORDS
from app.settings import store

router = APIRouter(tags=["buy-list"], dependencies=[Depends(current_user)])

ORDER_ACTIONS = ("buy", "get_ready")
WATCH_STATES = ("basing", "near_pivot")


class BuyItem(SetupRow):
    order: dict[str, Any] | None
    failed: list[str]  # the rules it fails, in a few words each
    plain: str | None
    watch_parts: list[dict[str, Any]]
    early_entry: dict[str, Any] | None
    watch_since: date | None


class MarketVerdict(BaseModel):
    state: str | None
    label: str | None
    words: str
    allows_buys: bool
    note: str


class BuyList(BaseModel):
    as_of: date | None
    market: MarketVerdict
    orders: list[BuyItem]
    watch: list[BuyItem]
    track: dict[str, Any]


def _item(setup: Setup, ticker: Ticker) -> BuyItem:
    verdict = setup.verdict or {}
    watch = setup.watch or {}
    return BuyItem(
        **_row(setup, ticker).model_dump(),
        order=verdict.get("order"),
        failed=[c["short"] for c in verdict.get("checks", []) if not c["passed"] and c["short"]],
        plain=verdict.get("plain"),
        watch_parts=watch.get("parts", []),
        early_entry=watch.get("early_entry"),
        watch_since=setup.watch_since,
    )


@router.get("/buy-list", response_model=BuyList)
async def buy_list(db: DbSession, limit: int = Query(30, ge=1, le=200)) -> BuyList:
    settings = await store.load(db)
    as_of = await db.scalar(select(ScanProgress.through).where(ScanProgress.stage == STAGE))
    regime = await db.scalar(
        select(MarketRegimeDaily)
        .where(MarketRegimeDaily.index_symbol == MARKET)
        .order_by(MarketRegimeDaily.date.desc())
        .limit(1)
    )
    state = None if regime is None else regime.state
    words = REGIME_WORDS.get(state or "", "unknown")
    allows = state == "confirmed_uptrend" or (
        state == "uptrend_under_pressure"
        and settings.decision_regimes == "confirmed_or_under_pressure"
    )
    if allows:
        note = "The market is rising: the rules allow new buys."
        if state == "uptrend_under_pressure":
            note = (
                f"The uptrend is under pressure: buy at "
                f"{settings.decision_under_pressure_size_pct:g}% of the normal size."
            )
    else:
        note = (
            f"No new buys while the market is {words}. Most breakouts fail then. The breakout "
            "watch shows what to be ready for when the market turns."
        )

    orders = (
        await db.execute(
            select(Setup, Ticker)
            .join(Ticker, Ticker.id == Setup.ticker_id)
            .where(Setup.active, Setup.action.in_(ORDER_ACTIONS))
            .order_by(
                case((Setup.action == "buy", 0), else_=1),
                Setup.watch_score.desc().nulls_last(),
                Setup.score.desc(),
            )
            .limit(limit)
        )
    ).all()
    watch = (
        await db.execute(
            select(Setup, Ticker)
            .join(Ticker, Ticker.id == Setup.ticker_id)
            .where(
                Setup.active,
                Setup.state.in_(WATCH_STATES),
                Setup.watch_score >= settings.watch_min_score,
            )
            .order_by(Setup.watch_score.desc(), Setup.score.desc())
            .limit(limit)
        )
    ).all()

    picks = (
        await db.execute(
            select(Signal.date, Signal.ticker_id).where(Signal.type == "breakout_watch")
        )
    ).all()
    first = min((d for d, _ in picks), default=None)
    breakouts: list[tuple[date, int]] = []
    sessions: list[date] = []
    if first is not None and as_of is not None:
        rows = await db.execute(
            select(Signal.date, Signal.ticker_id).where(
                Signal.type == "breakout", Signal.date > first
            )
        )
        breakouts = [(d, int(t)) for d, t in rows.all() if t is not None]
        sessions = sessions_between(first, as_of)
    track = track_record(
        [(d, int(t)) for d, t in picks if t is not None],
        breakouts,
        sessions,
        settings.watch_hit_sessions,
    )
    return BuyList(
        as_of=as_of,
        market=MarketVerdict(
            state=state,
            label=None if regime is None else regime.state.replace("_", " ").capitalize(),
            words=words,
            allows_buys=allows,
            note=note,
        ),
        orders=[_item(s, t) for s, t in orders],
        watch=[_item(s, t) for s, t in watch],
        track=track,
    )

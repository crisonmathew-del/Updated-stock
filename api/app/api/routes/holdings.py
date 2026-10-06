"""Holdings (spec §8.6): positions entered by hand (no broker connection; the platform never
trades). Each comes back with its price (live from the streamer when it has one, else the
latest close), P&L in dollars, % and R (against the initial stop), and the sell rules that
apply at that price.

- GET    /holdings              ?closed=true to include closed positions
- POST   /holdings              {symbol, entry_price, shares, initial_stop, stop?, opened_on?,
                                note?}
- PATCH  /holdings/{id}         {stop?, shares?, note?, exit_price?, closed_on?}: an exit
                                price closes it
- DELETE /holdings/{id}
A change tells the streamer to reload (it watches holdings' stops intraday).
"""

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.alerts.eod import HoldingDay, sell_warnings
from app.api.deps import AuthUser, DbSession, RedisClient, current_user
from app.core.calendar import MARKET_TZ
from app.data.loaders import query_frame
from app.intraday.live import REFRESH_CHANNEL, eod_through, live_quotes
from app.models import Holding, Setup, Ticker
from app.settings import store

router = APIRouter(prefix="/holdings", tags=["holdings"], dependencies=[Depends(current_user)])


class WarningOut(BaseModel):
    rule: str
    priority: str
    title: str
    body: str


class HoldingOut(BaseModel):
    id: int
    symbol: str
    name: str
    setup_id: int | None
    opened_on: date
    entry_price: float
    shares: int
    initial_stop: float
    stop: float
    note: str | None
    closed_on: date | None
    exit_price: float | None
    price: float | None
    price_at: datetime | date | None
    price_source: str | None  # live | close | exit
    day_change_pct: float | None
    pnl: float | None
    pnl_pct: float | None
    r: float | None
    risk_per_share: float
    position_value: float | None
    open_risk: float | None  # what's lost if the current stop is hit
    warnings: list[WarningOut]


class HoldingIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    entry_price: float = Field(gt=0)
    shares: int = Field(gt=0)
    initial_stop: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0)
    opened_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    setup_id: int | None = None


class HoldingPatch(BaseModel):
    stop: float | None = Field(default=None, gt=0)
    shares: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=2000)
    exit_price: float | None = Field(default=None, gt=0)
    closed_on: date | None = None


def _today() -> date:
    return datetime.now(MARKET_TZ).date()


async def _latest(db: DbSession, ticker_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Each stock's latest close (with the previous one) and its 21-day EMA and 50-day SMA."""
    if not ticker_ids:
        return {}
    ids = ",".join(str(int(t)) for t in ticker_ids)
    frame = await query_frame(
        db,
        "SELECT b.ticker_id, b.date, b.close, i.ema21, i.sma50, "
        "(SELECT p.close FROM daily_bars p WHERE p.ticker_id = b.ticker_id AND p.date < b.date "
        "ORDER BY p.date DESC LIMIT 1) AS prev_close "
        "FROM (SELECT DISTINCT ON (ticker_id) ticker_id, date, close FROM daily_bars "
        f"WHERE ticker_id IN ({ids}) ORDER BY ticker_id, date DESC) b "
        "LEFT JOIN indicators_daily i ON i.ticker_id = b.ticker_id AND i.date = b.date",
    )
    return {int(r["ticker_id"]): r for r in frame.iter_rows(named=True)}


async def _rows(
    db: DbSession, redis: RedisClient, holdings: list[tuple[Holding, Ticker]]
) -> list[HoldingOut]:
    settings = await store.load(db)
    latest = await _latest(db, [h.ticker_id for h, _ in holdings if h.closed_on is None])
    quotes = await live_quotes(
        redis, await eod_through(db), [t.symbol for h, t in holdings if h.closed_on is None]
    )
    out = []
    for h, t in holdings:
        risk = h.entry_price - h.initial_stop
        bar = latest.get(h.ticker_id)
        quote = quotes.get(t.symbol)
        price: float | None
        price_at: datetime | date | None
        if h.closed_on is not None:
            price, price_at, source, prev = h.exit_price, h.closed_on, "exit", None
        elif quote is not None:
            price, price_at, source = (
                float(quote["last"]),
                datetime.fromisoformat(quote["at"]),
                "live",
            )
            prev = quote.get("prev_close") or (bar["close"] if bar else None)
        elif bar is not None:
            price, price_at, source, prev = (
                float(bar["close"]),
                bar["date"],
                "close",
                bar["prev_close"],
            )
        else:
            price, price_at, source, prev = None, None, None, None
        warnings: list[WarningOut] = []
        if price is not None and h.closed_on is None:
            day = HoldingDay(
                symbol=t.symbol,
                entry=h.entry_price,
                stop=h.stop,
                initial_stop=h.initial_stop,
                close=price,
                ema21=bar["ema21"] if bar else None,
                sma50=bar["sma50"] if bar else None,
            )
            warnings = [
                WarningOut(rule=w.rule, priority=w.priority, title=w.title, body=w.body)
                for w in sell_warnings(day, settings, _today())
            ]
        pnl = None if price is None else (price - h.entry_price) * h.shares
        out.append(
            HoldingOut(
                id=h.id,
                symbol=t.symbol,
                name=t.name,
                setup_id=h.setup_id,
                opened_on=h.opened_on,
                entry_price=h.entry_price,
                shares=h.shares,
                initial_stop=h.initial_stop,
                stop=h.stop,
                note=h.note,
                closed_on=h.closed_on,
                exit_price=h.exit_price,
                price=price,
                price_at=price_at,
                price_source=source,
                day_change_pct=None
                if price is None or not prev or h.closed_on is not None
                else round((price / prev - 1) * 100, 2),
                pnl=None if pnl is None else round(pnl, 2),
                pnl_pct=None if price is None else round((price / h.entry_price - 1) * 100, 2),
                r=None if price is None or risk <= 0 else round((price - h.entry_price) / risk, 2),
                risk_per_share=round(risk, 4),
                position_value=None if price is None else round(price * h.shares, 2),
                open_risk=None
                if price is None or h.closed_on is not None
                else round(max(price - h.stop, 0) * h.shares, 2),
                warnings=warnings,
            )
        )
    return out


async def _own(db: DbSession, user: AuthUser, holding_id: int) -> tuple[Holding, Ticker]:
    row = (
        await db.execute(
            select(Holding, Ticker)
            .join(Ticker, Ticker.id == Holding.ticker_id)
            .where(Holding.id == holding_id)
        )
    ).first()
    if row is None or row[0].user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No holding {holding_id}.")
    return row[0], row[1]


@router.get("")
async def list_holdings(
    db: DbSession, redis: RedisClient, user: AuthUser, closed: bool = False
) -> list[HoldingOut]:
    query = (
        select(Holding, Ticker)
        .join(Ticker, Ticker.id == Holding.ticker_id)
        .where(Holding.user_id == user.id)
        .order_by(Holding.closed_on.is_not(None), Ticker.symbol, Holding.id)
    )
    if not closed:
        query = query.where(Holding.closed_on.is_(None))
    rows = [(h, t) for h, t in (await db.execute(query)).all()]
    return await _rows(db, redis, rows)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_holding(
    db: DbSession, redis: RedisClient, user: AuthUser, payload: HoldingIn
) -> HoldingOut:
    symbol = payload.symbol.strip().upper()
    ticker = await db.scalar(select(Ticker).where(Ticker.symbol == symbol))
    if ticker is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No stock {symbol}.")
    if payload.initial_stop >= payload.entry_price:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The stop must be below the entry price (long positions only).",
        )
    setup_id = payload.setup_id
    if setup_id is not None:
        setup = await db.get(Setup, setup_id)
        if setup is None or setup.ticker_id != ticker.id:
            setup_id = None
    holding = Holding(
        user_id=user.id,
        ticker_id=ticker.id,
        setup_id=setup_id,
        opened_on=payload.opened_on or _today(),
        entry_price=payload.entry_price,
        shares=payload.shares,
        initial_stop=payload.initial_stop,
        stop=payload.stop or payload.initial_stop,
        note=payload.note,
    )
    db.add(holding)
    await db.commit()
    await redis.publish(REFRESH_CHANNEL, "holdings")
    [row] = await _rows(db, redis, [(holding, ticker)])
    return row


@router.patch("/{holding_id}")
async def update_holding(
    db: DbSession, redis: RedisClient, user: AuthUser, holding_id: int, payload: HoldingPatch
) -> HoldingOut:
    holding, ticker = await _own(db, user, holding_id)
    changes = payload.model_dump(exclude_unset=True)
    for key in ("stop", "shares", "note"):
        if key in changes:
            setattr(holding, key, changes[key])
    if changes.get("exit_price") is not None:
        holding.exit_price = changes["exit_price"]
        holding.closed_on = changes.get("closed_on") or _today()
    elif "closed_on" in changes and changes["closed_on"] is None:
        holding.closed_on, holding.exit_price = None, None  # reopen
    await db.commit()
    await redis.publish(REFRESH_CHANNEL, "holdings")
    [row] = await _rows(db, redis, [(holding, ticker)])
    return row


@router.delete("/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding(
    db: DbSession, redis: RedisClient, user: AuthUser, holding_id: int
) -> Response:
    holding, _ = await _own(db, user, holding_id)
    await db.delete(holding)
    await db.commit()
    await redis.publish(REFRESH_CHANNEL, "holdings")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

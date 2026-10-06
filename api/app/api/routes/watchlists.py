"""Watchlists (spec §8.6): several named, ordered lists; each stock has a position and a note.

- GET    /watchlists                          the lists with their stocks and latest numbers
- POST   /watchlists                          create {name}
- PATCH  /watchlists/{id}                     rename / move {name?, position?}
- DELETE /watchlists/{id}
- POST   /watchlists/{id}/items               add {symbol, note?} (no-op if already there)
- POST   /watchlists/default/items            add to the first list, creating "Watchlist"
- PATCH  /watchlists/{id}/items/{symbol}      edit the note
- DELETE /watchlists/{id}/items/{symbol}
- PUT    /watchlists/{id}/order               {symbols: [...]} in the new order
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession, current_user
from app.data.loaders import query_frame
from app.models import Ticker, Watchlist, WatchlistItem

router = APIRouter(prefix="/watchlists", tags=["watchlists"], dependencies=[Depends(current_user)])

DEFAULT_NAME = "Watchlist"


class ItemOut(BaseModel):
    symbol: str
    name: str
    position: int
    note: str | None
    added_at: datetime
    date: date | None
    close: float | None
    change_pct: float | None
    rs_rating: int | None
    grade: str | None
    score: float | None
    state: str | None
    pivot: float | None
    readiness_pct: float | None


class WatchlistOut(BaseModel):
    id: int
    name: str
    position: int
    items: list[ItemOut]


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WatchlistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    position: int | None = Field(default=None, ge=0)


class ItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=2000)


class NotePatch(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class OrderIn(BaseModel):
    symbols: list[str] = Field(max_length=2000)


async def _own(db: DbSession, user: AuthUser, watchlist_id: int) -> Watchlist:
    found = await db.get(Watchlist, watchlist_id)
    if found is None or found.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No watchlist {watchlist_id}.")
    return found


async def _ticker(db: DbSession, symbol: str) -> Ticker:
    ticker = await db.scalar(
        select(Ticker)
        .where(Ticker.symbol == symbol.strip().upper())
        .order_by(Ticker.active.desc(), Ticker.id.desc())
        .limit(1)
    )
    if ticker is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No ticker {symbol.strip().upper()} in the universe."
        )
    return ticker


async def _check_name(db: DbSession, user: AuthUser, name: str, other: int | None = None) -> None:
    found = await db.scalar(
        select(Watchlist.id).where(Watchlist.user_id == user.id, Watchlist.name == name)
    )
    if found is not None and found != other:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f'You already have a watchlist called "{name}".'
        )


async def _items(db: DbSession, ids: list[int]) -> dict[int, list[ItemOut]]:
    """Each list's stocks in order, with the latest close, change and setup."""
    out: dict[int, list[ItemOut]] = {i: [] for i in ids}
    if not ids:
        return out
    frame = await query_frame(
        db,
        "SELECT w.watchlist_id, w.position, w.note, w.added_at, t.symbol, t.name, "
        "lb.date, lb.close, lb.prev_close, i.rs_rating, s.grade, s.score, s.state, s.pivot, "
        "s.readiness_pct FROM watchlist_items w JOIN tickers t ON t.id = w.ticker_id "
        "LEFT JOIN LATERAL (SELECT b.date, b.close, lag(b.close) OVER (ORDER BY b.date) "
        "AS prev_close FROM (SELECT date, close FROM daily_bars x WHERE x.ticker_id = t.id "
        "ORDER BY x.date DESC LIMIT 2) b ORDER BY b.date DESC LIMIT 1) lb ON true "
        "LEFT JOIN indicators_daily i ON i.ticker_id = t.id AND i.date = lb.date "
        "LEFT JOIN setups s ON s.ticker_id = t.id AND s.active "
        f"WHERE w.watchlist_id IN ({','.join(str(int(i)) for i in ids)}) "
        "ORDER BY w.watchlist_id, w.position, w.id",
    )
    for r in frame.iter_rows(named=True):
        prev = r["prev_close"]
        out[int(r["watchlist_id"])].append(
            ItemOut(
                symbol=r["symbol"],
                name=r["name"],
                position=r["position"],
                note=r["note"],
                added_at=r["added_at"],
                date=r["date"],
                close=r["close"],
                change_pct=round((r["close"] / prev - 1) * 100, 2) if prev else None,
                rs_rating=r["rs_rating"],
                grade=r["grade"],
                score=r["score"],
                state=r["state"],
                pivot=r["pivot"],
                readiness_pct=r["readiness_pct"],
            )
        )
    return out


async def _out(db: DbSession, user: AuthUser, only: int | None = None) -> list[WatchlistOut]:
    query = select(Watchlist).where(Watchlist.user_id == user.id)
    if only is not None:
        query = query.where(Watchlist.id == only)
    lists = (await db.scalars(query.order_by(Watchlist.position, Watchlist.id))).all()
    items = await _items(db, [w.id for w in lists])
    return [
        WatchlistOut(id=w.id, name=w.name, position=w.position, items=items[w.id]) for w in lists
    ]


@router.get("", response_model=list[WatchlistOut])
async def list_watchlists(db: DbSession, user: AuthUser) -> list[WatchlistOut]:
    return await _out(db, user)


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(db: DbSession, user: AuthUser, payload: WatchlistIn) -> WatchlistOut:
    name = payload.name.strip()
    await _check_name(db, user, name)
    last = await db.scalar(select(func.max(Watchlist.position)).where(Watchlist.user_id == user.id))
    created = Watchlist(user_id=user.id, name=name, position=(last or 0) + (last is not None))
    db.add(created)
    await db.commit()
    return (await _out(db, user, created.id))[0]


@router.patch("/{watchlist_id}", response_model=WatchlistOut)
async def update_watchlist(
    db: DbSession, user: AuthUser, watchlist_id: int, payload: WatchlistPatch
) -> WatchlistOut:
    found = await _own(db, user, watchlist_id)
    if payload.name is not None:
        await _check_name(db, user, payload.name.strip(), other=found.id)
        found.name = payload.name.strip()
    if payload.position is not None:
        found.position = payload.position
    await db.commit()
    return (await _out(db, user, found.id))[0]


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(db: DbSession, user: AuthUser, watchlist_id: int) -> Response:
    await db.delete(await _own(db, user, watchlist_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _add(db: DbSession, found: Watchlist, payload: ItemIn) -> None:
    ticker = await _ticker(db, payload.symbol)
    exists = await db.scalar(
        select(WatchlistItem.id).where(
            WatchlistItem.watchlist_id == found.id, WatchlistItem.ticker_id == ticker.id
        )
    )
    if exists is not None:
        return
    last = await db.scalar(
        select(func.max(WatchlistItem.position)).where(WatchlistItem.watchlist_id == found.id)
    )
    db.add(
        WatchlistItem(
            watchlist_id=found.id,
            ticker_id=ticker.id,
            position=0 if last is None else last + 1,
            note=(payload.note or "").strip() or None,
        )
    )
    await db.commit()


@router.post("/default/items", response_model=WatchlistOut)
async def add_to_default(db: DbSession, user: AuthUser, payload: ItemIn) -> WatchlistOut:
    """The `w` shortcut: add to the first watchlist, creating one called "Watchlist"."""
    found = await db.scalar(
        select(Watchlist)
        .where(Watchlist.user_id == user.id)
        .order_by(Watchlist.position, Watchlist.id)
        .limit(1)
    )
    if found is None:
        found = Watchlist(user_id=user.id, name=DEFAULT_NAME, position=0)
        db.add(found)
        await db.commit()
    await _add(db, found, payload)
    return (await _out(db, user, found.id))[0]


@router.post("/{watchlist_id}/items", response_model=WatchlistOut)
async def add_item(
    db: DbSession, user: AuthUser, watchlist_id: int, payload: ItemIn
) -> WatchlistOut:
    found = await _own(db, user, watchlist_id)
    await _add(db, found, payload)
    return (await _out(db, user, found.id))[0]


async def _item(db: DbSession, found: Watchlist, symbol: str) -> WatchlistItem:
    ticker = await _ticker(db, symbol)
    item = await db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == found.id, WatchlistItem.ticker_id == ticker.id
        )
    )
    if item is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{ticker.symbol} isn't on the watchlist {found.name}."
        )
    return item


@router.patch("/{watchlist_id}/items/{symbol}", response_model=WatchlistOut)
async def update_item(
    db: DbSession, user: AuthUser, watchlist_id: int, symbol: str, payload: NotePatch
) -> WatchlistOut:
    found = await _own(db, user, watchlist_id)
    item = await _item(db, found, symbol)
    item.note = (payload.note or "").strip() or None
    await db.commit()
    return (await _out(db, user, found.id))[0]


@router.delete("/{watchlist_id}/items/{symbol}", response_model=WatchlistOut)
async def remove_item(
    db: DbSession, user: AuthUser, watchlist_id: int, symbol: str
) -> WatchlistOut:
    found = await _own(db, user, watchlist_id)
    await db.delete(await _item(db, found, symbol))
    await db.commit()
    return (await _out(db, user, found.id))[0]


@router.put("/{watchlist_id}/order", response_model=WatchlistOut)
async def reorder(
    db: DbSession, user: AuthUser, watchlist_id: int, payload: OrderIn
) -> WatchlistOut:
    """Positions follow `symbols`; stocks not listed keep their order after them."""
    found = await _own(db, user, watchlist_id)
    rows = (
        await db.execute(
            select(WatchlistItem, Ticker.symbol)
            .join(Ticker, Ticker.id == WatchlistItem.ticker_id)
            .where(WatchlistItem.watchlist_id == found.id)
            .order_by(WatchlistItem.position, WatchlistItem.id)
        )
    ).all()
    wanted = [s.strip().upper() for s in payload.symbols]
    rank = {s: i for i, s in enumerate(wanted)}
    ordered = sorted(rows, key=lambda r: (rank.get(r[1], len(wanted)), r[0].position))
    for position, (item, _) in enumerate(ordered):
        item.position = position
    await db.commit()
    return (await _out(db, user, found.id))[0]

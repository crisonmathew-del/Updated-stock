"""Ticker search for the command palette (spec §8.1, §10: < 100 ms p95 on the server).

Ranks an exact symbol first, then symbol prefixes, then company names that start with the
query (or contain a word that does), then fuzzy matches (pg_trgm similarity) on either. Each
result carries the latest close, % change and the setup grade so the palette can show them.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession, current_user

router = APIRouter(tags=["search"], dependencies=[Depends(current_user)])

SIMILARITY_MIN = 0.25


class SearchHit(BaseModel):
    symbol: str
    name: str
    exchange: str
    type: str
    date: date | None
    close: float | None
    change_pct: float | None
    grade: str | None
    score: float | None
    state: str | None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


SEARCH_SQL = text(
    """
    WITH matches AS (
        SELECT t.id, t.symbol, t.name, t.exchange, t.type,
               CASE
                   WHEN t.symbol = :upper THEN 0
                   WHEN t.symbol LIKE :symbol_prefix ESCAPE '\\' THEN 1
                   WHEN t.name ILIKE :name_prefix ESCAPE '\\' THEN 2
                   WHEN t.name ILIKE :word ESCAPE '\\' THEN 3
                   ELSE 4
               END AS bucket,
               greatest(similarity(t.symbol, :upper), similarity(t.name, :query)) AS sim
        FROM tickers t
        WHERE t.active AND (
            t.symbol LIKE :symbol_prefix ESCAPE '\\'
            OR t.name ILIKE :name_prefix ESCAPE '\\'
            OR t.name ILIKE :word ESCAPE '\\'
            OR similarity(t.symbol, :upper) >= :min_sim
            OR similarity(t.name, :query) >= :min_sim
        )
        ORDER BY bucket, sim DESC, length(t.symbol), t.symbol
        LIMIT :limit
    ),
    last_bars AS (
        SELECT b.ticker_id, b.date, b.close,
               lag(b.close) OVER (PARTITION BY b.ticker_id ORDER BY b.date) AS prev_close,
               row_number() OVER (PARTITION BY b.ticker_id ORDER BY b.date DESC) AS n
        FROM daily_bars b
        WHERE b.ticker_id IN (SELECT id FROM matches)
          AND b.date >= (SELECT max(date) FROM daily_bars) - 14
    )
    SELECT m.symbol, m.name, m.exchange, m.type, lb.date, lb.close,
           CASE WHEN lb.prev_close > 0 THEN (lb.close / lb.prev_close - 1) * 100 END,
           s.grade, s.score, s.state
    FROM matches m
    LEFT JOIN last_bars lb ON lb.ticker_id = m.id AND lb.n = 1
    LEFT JOIN setups s ON s.ticker_id = m.id AND s.active
    ORDER BY m.bucket, m.sim DESC, length(m.symbol), m.symbol
    """
)


@router.get("/search", response_model=list[SearchHit])
async def search(
    db: DbSession,
    q: str = Query(..., min_length=1, max_length=60),
    limit: int = Query(8, ge=1, le=25),
) -> list[SearchHit]:
    query = q.strip()
    if not query:
        return []
    escaped = _escape_like(query)
    rows = await db.execute(
        SEARCH_SQL,
        {
            "query": query,
            "upper": query.upper(),
            "symbol_prefix": f"{_escape_like(query.upper())}%",
            "name_prefix": f"{escaped}%",
            "word": f"% {escaped}%",
            "min_sim": SIMILARITY_MIN,
            "limit": limit,
        },
    )
    return [
        SearchHit(
            symbol=symbol,
            name=name,
            exchange=exchange,
            type=kind,
            date=day,
            close=close,
            change_pct=None if change is None else round(float(change), 2),
            grade=grade,
            score=score,
            state=state,
        )
        for symbol, name, exchange, kind, day, close, change, grade, score, state in rows.all()
    ]

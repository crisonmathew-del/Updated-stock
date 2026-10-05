"""Pattern detections and the owner's false-positive review (spec §15 Phase 3 acceptance).

- GET  /admin/patterns              recent detections (filter by type/status)
- GET  /admin/patterns/sample       a random sample spread across pattern types (seeded)
- GET  /admin/patterns/review-stats verdicts and the false-positive rate per type
- GET  /admin/patterns/{id}         one detection
- GET  /admin/patterns/{id}/chart.png  its chart (light, or ?theme=dark)
- PUT/DELETE /admin/patterns/{id}/review  record or clear a verdict
"""

import asyncio
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import DbSession, current_user
from app.core.calendar import sessions_back
from app.data.loaders import read_frame
from app.models import Pattern, PatternReview, Ticker
from app.patterns.chart import render_pattern_chart
from app.patterns.types import LABELS, PatternType

router = APIRouter(
    prefix="/admin/patterns", tags=["patterns"], dependencies=[Depends(current_user)]
)

Verdict = Literal["correct", "wrong", "unsure"]
CHART_SESSIONS_BEFORE = 80


class ReviewOut(BaseModel):
    verdict: Verdict
    note: str | None
    reviewed_at: datetime


class PatternOut(BaseModel):
    id: int
    symbol: str
    name: str
    type: str
    type_label: str
    timeframe: str
    start_date: date
    end_date: date
    pivot: float
    base_low: float | None
    depth_pct: float | None
    duration_weeks: float
    quality: float
    base_number: int | None
    status: str
    status_date: date
    first_detected: date
    last_seen: date
    components: list[dict[str, Any]]
    swings: list[dict[str, Any]]
    contractions: list[dict[str, Any]]
    details: dict[str, Any]
    review: ReviewOut | None


class ReviewIn(BaseModel):
    verdict: Verdict
    note: str | None = Field(default=None, max_length=2000)


class TypeStats(BaseModel):
    type: str
    type_label: str
    detected: int
    reviewed: int
    correct: int
    wrong: int
    unsure: int
    false_positive_rate: float | None  # wrong / (correct + wrong)


class ReviewStats(BaseModel):
    types: list[TypeStats]
    reviewed: int
    false_positive_rate: float | None


def _label(kind: str) -> str:
    try:
        return LABELS[PatternType(kind)]
    except ValueError:
        return kind


def pattern_query() -> Select[Pattern, Ticker, PatternReview]:
    return (
        select(Pattern, Ticker, PatternReview)
        .join(Ticker, Ticker.id == Pattern.ticker_id)
        .outerjoin(PatternReview, PatternReview.pattern_id == Pattern.id)
    )


def to_out(pattern: Pattern, ticker: Ticker, review: PatternReview | None) -> PatternOut:
    return PatternOut(
        id=pattern.id,
        symbol=ticker.symbol,
        name=ticker.name,
        type=pattern.type,
        type_label=_label(pattern.type),
        timeframe=pattern.timeframe,
        start_date=pattern.start_date,
        end_date=pattern.end_date,
        pivot=pattern.pivot,
        base_low=pattern.base_low,
        depth_pct=pattern.depth_pct,
        duration_weeks=pattern.duration_weeks,
        quality=pattern.quality,
        base_number=pattern.base_number,
        status=pattern.status,
        status_date=pattern.status_date,
        first_detected=pattern.first_detected,
        last_seen=pattern.last_seen,
        components=pattern.components,
        swings=pattern.swings,
        contractions=pattern.contractions,
        details=pattern.details,
        review=ReviewOut(verdict=review.verdict, note=review.note, reviewed_at=review.reviewed_at)
        if review
        else None,
    )


@router.get("", response_model=list[PatternOut])
async def list_patterns(
    db: DbSession,
    kind: Annotated[PatternType | None, Query(alias="type")] = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
) -> list[PatternOut]:
    query = pattern_query().order_by(Pattern.last_seen.desc(), Pattern.quality.desc()).limit(limit)
    if kind is not None:
        query = query.where(Pattern.type == kind.value)
    if status_filter:
        query = query.where(Pattern.status == status_filter)
    return [to_out(*row) for row in (await db.execute(query)).all()]


@router.get("/sample", response_model=list[PatternOut])
async def review_sample(
    db: DbSession,
    size: int = Query(20, ge=1, le=100),
    seed: int = Query(1, ge=0),
    unreviewed: bool = False,
) -> list[PatternOut]:
    """`size` detections chosen at random but spread across pattern types (one of each type
    in turn), reproducible for a given seed."""
    shuffle = func.md5(func.concat(Pattern.id, "-", seed))
    ranked = select(
        Pattern.id,
        func.row_number().over(partition_by=Pattern.type, order_by=shuffle).label("turn"),
        shuffle.label("shuffle"),
    )
    if unreviewed:
        ranked = ranked.outerjoin(PatternReview, PatternReview.pattern_id == Pattern.id).where(
            PatternReview.pattern_id.is_(None)
        )
    sub = ranked.subquery()
    ids = (await db.scalars(select(sub.c.id).order_by(sub.c.turn, sub.c.shuffle).limit(size))).all()
    if not ids:
        return []
    rows = {
        p.id: (p, t, r)
        for p, t, r in (await db.execute(pattern_query().where(Pattern.id.in_(ids)))).all()
    }
    return [to_out(*rows[i]) for i in ids]


@router.get("/review-stats", response_model=ReviewStats)
async def review_stats(db: DbSession) -> ReviewStats:
    rows = (
        await db.execute(
            select(
                Pattern.type,
                func.count(Pattern.id),
                func.count(PatternReview.pattern_id),
                func.count().filter(PatternReview.verdict == "correct"),
                func.count().filter(PatternReview.verdict == "wrong"),
                func.count().filter(PatternReview.verdict == "unsure"),
            )
            .outerjoin(PatternReview, PatternReview.pattern_id == Pattern.id)
            .group_by(Pattern.type)
            .order_by(Pattern.type)
        )
    ).all()

    def rate(correct: int, wrong: int) -> float | None:
        return round(wrong / (correct + wrong), 3) if correct + wrong else None

    types = [
        TypeStats(
            type=kind,
            type_label=_label(kind),
            detected=detected,
            reviewed=reviewed,
            correct=correct,
            wrong=wrong,
            unsure=unsure,
            false_positive_rate=rate(correct, wrong),
        )
        for kind, detected, reviewed, correct, wrong, unsure in rows
    ]
    total_correct = sum(t.correct for t in types)
    total_wrong = sum(t.wrong for t in types)
    return ReviewStats(
        types=types,
        reviewed=sum(t.reviewed for t in types),
        false_positive_rate=rate(total_correct, total_wrong),
    )


async def _one(db: DbSession, pattern_id: int) -> tuple[Pattern, Ticker, PatternReview | None]:
    row = (await db.execute(pattern_query().where(Pattern.id == pattern_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No detection with id {pattern_id}.")
    return row[0], row[1], row[2]


@router.get("/{pattern_id}", response_model=PatternOut)
async def get_pattern(db: DbSession, pattern_id: int) -> PatternOut:
    return to_out(*await _one(db, pattern_id))


@router.get("/{pattern_id}/chart.png", response_class=Response)
async def pattern_chart(
    db: DbSession, pattern_id: int, theme: Literal["light", "dark"] = "light"
) -> Response:
    pattern, ticker, _ = await _one(db, pattern_id)
    first = sessions_back(pattern.start_date, CHART_SESSIONS_BEFORE)
    frame = await read_frame(
        "SELECT b.date, b.open, b.high, b.low, b.close, b.volume, i.sma50, i.sma150, i.sma200, "
        "i.avg_volume_50 FROM daily_bars b LEFT JOIN indicators_daily i "
        "ON i.ticker_id = b.ticker_id AND i.date = b.date "
        f"WHERE b.ticker_id = {int(ticker.id)} AND b.date BETWEEN '{first.isoformat()}' "
        f"AND '{pattern.last_seen.isoformat()}' ORDER BY b.date"
    )
    if frame.is_empty():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No price history for this detection.")
    row = {
        "start_date": pattern.start_date,
        "end_date": pattern.end_date,
        "last_seen": pattern.last_seen,
        "pivot": pattern.pivot,
        "base_low": pattern.base_low,
        "swings": pattern.swings,
        "contractions": pattern.contractions,
    }
    title = (
        f"{ticker.symbol} · {_label(pattern.type)} · {pattern.status.replace('_', ' ')} · "
        f"quality {pattern.quality:.0f}"
    )
    png = await asyncio.to_thread(
        render_pattern_chart, frame, row, title=title, dark=theme == "dark"
    )
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.put("/{pattern_id}/review", response_model=PatternOut)
async def review_pattern(db: DbSession, pattern_id: int, body: ReviewIn) -> PatternOut:
    await _one(db, pattern_id)
    stmt = insert(PatternReview).values(pattern_id=pattern_id, verdict=body.verdict, note=body.note)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["pattern_id"],
            set_={
                "verdict": stmt.excluded.verdict,
                "note": stmt.excluded.note,
                "reviewed_at": text("now()"),
            },
        )
    )
    await db.commit()
    db.expire_all()
    return to_out(*await _one(db, pattern_id))


@router.delete("/{pattern_id}/review", status_code=status.HTTP_204_NO_CONTENT)
async def clear_review(db: DbSession, pattern_id: int) -> None:
    await _one(db, pattern_id)
    review = await db.get(PatternReview, pattern_id)
    if review is not None:
        await db.delete(review)
        await db.commit()

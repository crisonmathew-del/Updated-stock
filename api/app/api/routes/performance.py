"""Signal performance (spec §8.8): every signal type by score bucket and market regime, from
the signal log and the outcomes tracked after each signal (see app.scanner.performance).

- GET /performance  ?horizon=5|10|20|60 (sessions, default 20)&since=YYYY-MM-DD (default: all)
"""

import bisect
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import DbSession, current_user
from app.core.calendar import sessions_between
from app.data.loaders import query_frame
from app.scanner.performance import HORIZONS, SignalRow, summarize

router = APIRouter(tags=["performance"], dependencies=[Depends(current_user)])


class PerformanceOut(BaseModel):
    horizon: int
    since: date | None
    first: date | None  # the oldest signal counted
    last: date | None
    total: dict[str, Any]
    types: list[dict[str, Any]]


@router.get("/performance")
async def performance(
    db: DbSession,
    horizon: int = 20,
    since: Annotated[date | None, Query()] = None,
) -> PerformanceOut:
    if horizon not in HORIZONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The horizon is one of {', '.join(str(h) for h in HORIZONS)} sessions.",
        )
    where = f"WHERE s.date >= '{since.isoformat()}'" if since else ""
    frame = await query_frame(
        db,
        "SELECT s.type, s.date, s.grade, s.score IS NOT NULL AS scored, "
        "s.context->'market'->>'state' AS regime, s.price, "
        "s.entry, s.stop, o.ret_5, o.ret_10, o.ret_20, o.ret_60, "
        "coalesce(o.sessions_observed, 0) AS observed, o.stop_hit_on, o.gain_20_on "
        f"FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id {where} "
        "ORDER BY s.date",
    )
    rows: list[SignalRow] = []
    first = last = None
    if frame.height:
        first, last = frame["date"][0], frame["date"][-1]
        latest = max(
            [last, *(d for c in ("stop_hit_on", "gain_20_on") for d in frame[c].drop_nulls())]
        )
        calendar = sessions_between(first, latest)

        def after(start: date, hit: date | None) -> int | None:
            if hit is None:
                return None
            return bisect.bisect_right(calendar, hit) - bisect.bisect_right(calendar, start)

        for r in frame.iter_rows(named=True):
            rows.append(
                SignalRow(
                    type=r["type"],
                    date=r["date"],
                    grade=r["grade"],
                    regime=r["regime"],
                    price=r["price"],
                    entry=r["entry"],
                    stop=r["stop"],
                    returns={h: r[f"ret_{h}"] for h in HORIZONS},
                    observed=int(r["observed"]),
                    stop_hit_after=after(r["date"], r["stop_hit_on"]),
                    gain_20_after=after(r["date"], r["gain_20_on"]),
                    scored=bool(r["scored"]),
                )
            )
    summary = summarize(rows, horizon)
    return PerformanceOut(
        horizon=horizon,
        since=since,
        first=first,
        last=last,
        total=summary["total"],
        types=summary["types"],
    )

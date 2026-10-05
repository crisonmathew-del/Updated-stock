"""Grades and patterns inside the EOD analytics pipeline: a stock draws a VCP, then breaks out,
ages out or fails; every stock gets a grade row; old as-of runs never overwrite newer rows."""

from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_between
from app.core.redis import get_redis
from app.data.backfill import run_backfill
from app.data.bars import upsert_bars
from app.data.universe import plan_universe, sync_tickers
from app.models import FundamentalGrade, FundamentalsAnnual, FundamentalsQuarterly, Pattern, Ticker
from app.providers.base import Bar, PriceHistory
from app.scanner.detection import run_detection
from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.fakes import FakePrices, make_history
from tests.test_patterns import VCP, VCP_VOLUME
from tests.test_universe import listed

SETTINGS = AppSettings()
START = date(2024, 1, 2)
DAYS = sessions_between(START, date(2026, 12, 31))


def drawn(
    symbol: str, waypoints: Sequence[tuple[int, float]], volumes: Sequence[tuple[int, float]]
) -> PriceHistory:
    """The same shape the pattern tests use: straight lines between waypoints, 1% range."""
    n = waypoints[-1][0] + 1
    xs, ys = zip(*waypoints, strict=True)
    close = np.interp(np.arange(n), xs, ys)
    level = np.ones(n)
    for start, multiple in volumes:
        level[start:] = multiple
    bars = []
    for i in range(n):
        o = close[i - 1] if i else close[0]
        bars.append(
            Bar(
                DAYS[i],
                float(o),
                float(max(o, close[i]) * 1.005),
                float(min(o, close[i]) * 0.995),
                float(close[i]),
                int(level[i] * 1_000_000),
            )
        )
    return PriceHistory(symbol, bars)


async def seed(db: AsyncSession, waypoints: Sequence[tuple[int, float]]) -> dict[str, int]:
    plan = plan_universe(listed("SPOT", "AAPL"))
    await sync_tickers(db, plan, START)
    last = DAYS[waypoints[-1][0]]
    histories = {
        symbol: make_history(symbol, START, last, first_close=400, step=0.2)
        for symbol in plan
        if symbol != "SPOT"
    }
    histories["SPOT"] = drawn("SPOT", waypoints, VCP_VOLUME)
    await run_backfill(db, FakePrices(histories), get_redis(), today=last, end=last, years=3)
    return {t.symbol: t.id for t in (await db.scalars(select(Ticker))).all()}


async def extend(
    db: AsyncSession, ticker_id: int, waypoints: Sequence[tuple[int, float]], first: int
) -> date:
    history = drawn("SPOT", waypoints, VCP_VOLUME)
    await upsert_bars(db, {ticker_id: PriceHistory("SPOT", history.bars[first:])}, "test")
    others = (await db.scalars(select(Ticker).where(Ticker.symbol != "SPOT"))).all()
    last = DAYS[waypoints[-1][0]]
    for t in others:
        h = make_history(t.symbol, START, last, first_close=400, step=0.2)
        await upsert_bars(db, {t.id: PriceHistory(t.symbol, h.bars[first:])}, "test")
    await db.commit()
    return last


async def vcp_row(db: AsyncSession, ticker_id: int) -> Pattern:
    db.expire_all()
    return (
        await db.execute(
            select(Pattern).where(Pattern.ticker_id == ticker_id, Pattern.type == "vcp")
        )
    ).scalar_one()


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_vcp_forms_then_breaks_out_then_stays_broken_out(db: AsyncSession) -> None:
    ids = await seed(db, VCP)
    day0 = DAYS[332]
    result = await run_analytics(db, SETTINGS, through=day0)

    stats = result.stats()
    assert stats["patterns"]["by_type"].get("vcp") == 1
    row = await vcp_row(db, ids["SPOT"])
    assert (row.status, row.start_date, row.last_seen) == ("forming", DAYS[260], day0)
    assert row.pivot == pytest.approx(92.46)
    assert [round(c["depth_pct"], 1) for c in row.contractions] == [24.8, 13.9, 7.0, 4.0]
    assert sum(c["max_points"] for c in row.components) == 100
    # Every stock with a bar gets a grade row (no fundamentals loaded: "n/a").
    grades = (await db.execute(select(FundamentalGrade.ticker_id, FundamentalGrade.grade))).all()
    assert {tid for tid, _ in grades} >= {ids["SPOT"], ids["AAPL"]}
    assert stats["grades"] == {"n/a": len(grades)}

    day1 = await extend(db, ids["SPOT"], [*VCP, (334, 94.0)], 333)
    await run_analytics(db, SETTINGS, through=day1)
    row = await vcp_row(db, ids["SPOT"])
    assert (row.status, row.status_date) == ("broken_out", day1)
    assert row.details["breakout_date"] == DAYS[333].isoformat()  # the first close above
    assert row.first_detected == day0

    day5 = await extend(db, ids["SPOT"], [*VCP, (334, 94.0), (338, 98.0)], 335)
    await run_analytics(db, SETTINGS, through=day5)
    row = await vcp_row(db, ids["SPOT"])
    assert (row.status, row.last_seen) == ("broken_out", DAYS[334])  # no longer detected, kept


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_base_that_undercuts_its_low_is_marked_failed(db: AsyncSession) -> None:
    ids = await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    crash = await extend(db, ids["SPOT"], [*VCP, (336, 70.0)], 333)
    await run_analytics(db, SETTINGS, through=crash)
    row = await vcp_row(db, ids["SPOT"])
    assert (row.status, row.status_date) == ("failed", crash)  # close 70 < base low 75.62


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_an_older_as_of_run_never_overwrites_a_newer_detection(db: AsyncSession) -> None:
    ids = await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    newer = await vcp_row(db, ids["SPOT"])
    # Re-run detection as of three sessions earlier: same base (same start), older view.
    _, run = await run_detection(db, SETTINGS, DAYS[329], [ids["SPOT"]])
    assert any(m.type == "vcp" for _, m in run.matches)
    row = await vcp_row(db, ids["SPOT"])
    assert (row.last_seen, row.end_date, row.quality) == (
        newer.last_seen,
        newer.end_date,
        newer.quality,
    )


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_grades_use_stored_fundamentals_as_of_the_scan_date(db: AsyncSession) -> None:
    ids = await seed(db, VCP)
    day0 = DAYS[332]
    aapl = ids["AAPL"]

    def q(end: date, eps: float, revenue: float, reported: date) -> dict[str, object]:
        return {
            "ticker_id": aapl,
            "period_end": end,
            "reported_date": reported,
            "period_start": end - timedelta(days=90),
            "eps_diluted": eps,
            "revenue": revenue,
            "source": "test",
        }

    year_ago = day0 - timedelta(days=365 + 40)
    latest = day0 - timedelta(days=40)
    await db.execute(
        insert(FundamentalsQuarterly),
        [
            q(year_ago, 0.50, 100e6, year_ago + timedelta(days=30)),
            q(latest, 0.80, 130e6, latest + timedelta(days=30)),
            # Filed after the scan date: must not count.
            q(latest, 0.10, 50e6, day0 + timedelta(days=5)),
        ],
    )
    await db.execute(
        insert(FundamentalsAnnual),
        [
            {
                "ticker_id": aapl,
                "period_end": date(2024, 12, 31),
                "reported_date": date(2025, 2, 15),
                "period_start": date(2024, 1, 1),
                "eps_diluted": 2.0,
                "source": "test",
            }
        ],
    )
    await db.commit()
    await run_analytics(db, SETTINGS, through=day0)
    row = (
        await db.execute(
            select(FundamentalGrade).where(
                FundamentalGrade.ticker_id == aapl, FundamentalGrade.date == day0
            )
        )
    ).scalar_one()
    eps = next(c for c in row.components if c["key"] == "eps_growth")
    assert eps["points"] == 25  # 0.80 vs 0.50 = +60%: the later 0.10 version is invisible
    assert row.basis == "quarterly"


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_patterns_job_lists_detections_for_named_stocks(db: AsyncSession) -> None:
    from app.data import jobs

    await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    stats = await jobs.patterns_job("cli", as_of=DAYS[332], symbols=["spot", "NOPE"])
    assert stats["unknown_symbols"] == ["NOPE"]
    vcp = [d for d in stats["detections"] if d["type"] == "vcp"]
    assert vcp == [
        {
            "symbol": "SPOT",
            "type": "vcp",
            "start": DAYS[260].isoformat(),
            "end": DAYS[332].isoformat(),
            "pivot": 92.46,
            "depth_pct": 24.8,
            "status": "forming",
            "quality": vcp[0]["quality"],
        }
    ]

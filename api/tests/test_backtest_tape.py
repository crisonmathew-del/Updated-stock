"""Stage 1 replays the EOD pipeline: walking the synthetic market (tests.backtest_market) through
the backtest gives the same signals, session by session, as running the nightly scan on each
session in turn, and the same setups waiting below their pivots with the same plans."""

from typing import Any

import polars as pl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.context import load_market
from app.backtest.tape import base_cell, build_tape
from app.models import Setup, Signal
from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.backtest_market import seed_market
from tests.test_detection_pipeline import DAYS

SETTINGS = AppSettings()
FIRST, LAST = 326, 392


async def pipeline_log(db: AsyncSession) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Run the nightly scan on each session from FIRST to LAST; return its signals and, per
    session, the setups waiting below their pivot with a plan."""
    waiting: list[tuple[Any, ...]] = []
    for n in range(FIRST, LAST + 1):
        day = DAYS[n]
        await run_analytics(db, SETTINGS, through=day)
        db.expire_all()
        for s in (
            await db.scalars(
                select(Setup).where(
                    Setup.active, Setup.state.in_(("basing", "near_pivot")), Setup.as_of == day
                )
            )
        ).all():
            if s.trade_plan is not None:
                plan = s.trade_plan
                waiting.append(
                    (
                        day,
                        s.ticker_id,
                        s.state,
                        s.pivot,
                        plan["entry"],
                        plan["stop"],
                        s.score,
                        s.grade,
                    )
                )
    signals = (
        await db.execute(
            select(Signal.date, Signal.ticker_id, Signal.type)
            .where(Signal.type != "regime_change")
            .order_by(Signal.date, Signal.ticker_id, Signal.type)
        )
    ).all()
    return [tuple(s) for s in signals], sorted(waiting)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_the_walk_reproduces_the_nightly_scan(db: AsyncSession) -> None:
    ids = await seed_market(db)
    signals, waiting = await pipeline_log(db)
    # It saw real work: breakouts that held and one that failed, setups waiting on 4 stocks.
    kinds = {t for _, _, t in signals}
    assert {"breakout", "near_pivot", "failed", "extended", "new_top_setup"} <= kinds
    assert len(waiting) > 40
    assert {t for _, t, *_ in waiting} == {ids[s] for s in ("SPOT", "A", "GOOG", "O")}

    start, end = DAYS[FIRST], DAYS[LAST]
    market = await load_market(start, end)
    tape = build_tape(market, SETTINGS, [base_cell(SETTINGS)], start, end, workers=1)

    walked = [tuple(r) for r in tape.signals.iter_rows()]
    assert walked == signals
    candidates = sorted(
        (
            r["date"],
            r["ticker_id"],
            r["state"],
            r["pivot"],
            r["entry"],
            r["stop"],
            r["score"],
            r["grade"],
        )
        for r in tape.candidates.iter_rows(named=True)
    )
    assert candidates == waiting
    breakouts = {(r["date"], r["ticker_id"]) for r in tape.breakouts.iter_rows(named=True)}
    assert breakouts == {(d, t) for d, t, kind in signals if kind == "breakout"}
    assert (DAYS[333], ids["SPOT"]) in breakouts
    # The screener fields for saved screens: one row per stock-session with a candidate.
    assert tape.stock_days.height == len({(d, t) for d, t, *_ in waiting})

    # No lookahead inside the walk: stopping at session 360 (so nothing later is even loaded)
    # gives exactly the longer walk's tape up to 360.
    cut = DAYS[360]
    short = build_tape(
        await load_market(start, cut), SETTINGS, [base_cell(SETTINGS)], start, cut, workers=1
    )
    for name in ("candidates", "breakouts", "signals", "stock_days"):
        early = getattr(tape, name).filter(pl.col("date") <= cut)
        assert getattr(short, name).equals(early), name

    # Worker processes give the same tape.
    parallel = build_tape(market, SETTINGS, [base_cell(SETTINGS)], start, end, workers=2)
    for name in ("candidates", "breakouts", "signals", "stock_days"):
        assert getattr(parallel, name).equals(getattr(tape, name)), name

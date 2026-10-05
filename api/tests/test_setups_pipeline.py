"""The setups stage inside the EOD pipeline: a drawn VCP (tests.test_patterns) becomes a setup,
breaks out on heavy volume, is logged as signals; re-running a session changes nothing; and the
spec §12 lookahead guard: the same sessions give the same setups and signals whether or not
later prices are already in the database."""

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base
from app.core.redis import get_redis
from app.data.backfill import run_backfill
from app.data.bars import upsert_bars
from app.data.universe import plan_universe, sync_tickers
from app.models import ScanProgress, Setup, SetupTransition, Signal, Ticker
from app.scanner.daily import MAX_CATCH_UP, pending_sessions
from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.fakes import FakePrices, make_history
from tests.test_detection_pipeline import DAYS, START, drawn
from tests.test_patterns import VCP, VCP_VOLUME
from tests.test_universe import listed

SETTINGS = AppSettings()
BREAKOUT = [*VCP, (334, 94.0)]  # session 333 closes 92.75, above the pivot 92.46
BREAKOUT_VOLUME = [*VCP_VOLUME, (333, 2.0), (334, 1.0)]  # 2M shares: ~3× the 50-day average
LATER = [*BREAKOUT, (345, 99.0)]


def test_pending_sessions_catch_up_in_order_with_a_cap() -> None:
    d = DAYS[300]
    assert pending_sessions(None, d) == ([d], 0)  # first run: the latest session only
    assert pending_sessions(d, d) == ([d], 0)  # a re-run
    assert pending_sessions(DAYS[297], d) == (DAYS[298:301], 0)
    days, skipped = pending_sessions(DAYS[280], d)
    assert (days, skipped) == (DAYS[301 - MAX_CATCH_UP : 301], 20 - MAX_CATCH_UP)


async def seed(db: AsyncSession, waypoints: Sequence[tuple[int, float]]) -> int:
    plan = plan_universe(listed("SPOT", "AAPL"))
    await sync_tickers(db, plan, START)
    last = DAYS[waypoints[-1][0]]
    histories = {
        symbol: make_history(symbol, START, last, first_close=400, step=0.2)
        for symbol in plan
        if symbol != "SPOT"
    }
    histories["SPOT"] = drawn("SPOT", waypoints, BREAKOUT_VOLUME)
    await run_backfill(db, FakePrices(histories), get_redis(), today=last, end=last, years=3)
    spot = await db.scalar(select(Ticker.id).where(Ticker.symbol == "SPOT"))
    assert spot is not None
    return int(spot)


async def extend(db: AsyncSession, waypoints: Sequence[tuple[int, float]], first: int) -> None:
    last = DAYS[waypoints[-1][0]]
    for t in (await db.scalars(select(Ticker))).all():
        if t.symbol == "SPOT":
            history = drawn("SPOT", waypoints, BREAKOUT_VOLUME)
        else:
            history = make_history(t.symbol, START, last, first_close=400, step=0.2)
        await upsert_bars(db, {t.id: type(history)(t.symbol, history.bars[first:])}, "test")
    await db.commit()


async def spot_setup(db: AsyncSession, spot: int) -> Setup:
    db.expire_all()
    return (
        await db.execute(select(Setup).where(Setup.ticker_id == spot, Setup.active))
    ).scalar_one()


async def snapshot(db: AsyncSession) -> dict[str, Any]:
    """Everything the stage wrote, keyed by symbol (ids and timestamps left out)."""
    db.expire_all()
    symbols = dict((await db.execute(select(Ticker.id, Ticker.symbol))).all())
    setups = (await db.scalars(select(Setup).order_by(Setup.ticker_id, Setup.first_seen))).all()
    skip = {"id", "created_at", "updated_at", "previous", "pattern_id", "ticker_id"}

    def row(obj: object, columns: Sequence[str]) -> dict[str, Any]:
        return {c: getattr(obj, c) for c in columns if c not in skip}

    setup_columns = [c.name for c in Setup.__table__.columns]
    by_id = {s.id: symbols[s.ticker_id] for s in setups}
    transitions = (await db.scalars(select(SetupTransition).order_by(SetupTransition.id))).all()
    signals = (
        await db.scalars(select(Signal).order_by(Signal.date, Signal.type, Signal.ticker_id))
    ).all()
    return {
        "setups": [(symbols[s.ticker_id], row(s, setup_columns)) for s in setups],
        "transitions": sorted(
            (by_id[x.setup_id], x.date, x.from_state, x.to_state, x.reason) for x in transitions
        ),
        "signals": [
            (
                None if s.ticker_id is None else symbols[s.ticker_id],
                row(s, [c.name for c in Signal.__table__.columns if c.name != "setup_id"]),
            )
            for s in signals
        ],
    }


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_vcp_setup_breaks_out_and_is_logged(db: AsyncSession) -> None:
    spot = await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    setup = await spot_setup(db, spot)
    assert (setup.kind, setup.pattern_type, setup.state) == ("base", "vcp", "near_pivot")
    assert setup.pivot == pytest.approx(92.46)
    assert setup.trade_plan is not None
    # Pivot 92.46 + 0.10; the last contraction's low 88.80 less 0.1%, rounded down.
    assert (setup.trade_plan["entry"], setup.trade_plan["stop"]) == (92.56, 88.71)
    assert [c["key"] for c in setup.components] == [
        "trend",
        "relative_strength",
        "fundamentals",
        "pattern",
        "group",
        "accumulation",
    ]
    progress = await db.get(ScanProgress, "setups")
    assert progress is not None
    assert progress.through == DAYS[332]

    await extend(db, BREAKOUT, 333)
    result = await run_analytics(db, SETTINGS, through=DAYS[334])
    assert result.stats()["setups"]["sessions"] == [DAYS[333].isoformat(), DAYS[334].isoformat()]
    setup = await spot_setup(db, spot)
    assert (setup.state, setup.breakout_date) == ("breakout", DAYS[333])
    transitions = (
        await db.execute(
            select(SetupTransition.date, SetupTransition.to_state)
            .where(SetupTransition.setup_id == setup.id)
            .order_by(SetupTransition.id)
        )
    ).all()
    assert transitions == [(DAYS[332], "near_pivot"), (DAYS[333], "breakout")]
    spot_signals = (
        await db.execute(
            select(Signal.date, Signal.type, Signal.entry, Signal.stop)
            .where(Signal.ticker_id == spot)
            .order_by(Signal.date)
        )
    ).all()
    assert (DAYS[332], "near_pivot", 92.56, 88.71) in spot_signals
    assert (DAYS[333], "breakout", 92.56, 88.71) in spot_signals
    breakout = (
        await db.execute(select(Signal).where(Signal.type == "breakout", Signal.ticker_id == spot))
    ).scalar_one()
    assert breakout.summary.startswith("Breakout confirmed: Closed 0.3% above the pivot 92.46")
    assert breakout.context["setup"]["state"] == "breakout"
    assert breakout.context["trade_plan"]["entry"] == 92.56
    # The near-pivot signal (332) has one session of outcome so far (333 closed 92.75).
    outcome = (
        await db.execute(
            text(
                "SELECT o.sessions_observed, o.ret_1 FROM signal_outcomes o JOIN signals s "
                "ON s.id = o.signal_id WHERE s.type = 'near_pivot' AND s.ticker_id = :t"
            ),
            {"t": spot},
        )
    ).one()
    assert outcome == (2, pytest.approx((92.75 / 91.5 - 1) * 100, abs=0.01))

    # Re-running the latest session rebuilds it from the snapshot: nothing changes.
    before = await snapshot(db)
    await run_analytics(db, SETTINGS, through=DAYS[334])
    assert await snapshot(db) == before


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_later_prices_in_the_database_change_nothing(db: AsyncSession) -> None:
    """Spec §12: run sessions 332-334 with prices through 345 already loaded, then again with
    only what was known each day; setups, transitions and signals must be identical."""
    await seed(db, LATER)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    await run_analytics(db, SETTINGS, through=DAYS[334])
    with_later = await snapshot(db)
    assert any(s["state"] == "breakout" for _, s in with_later["setups"])

    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    await db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await db.commit()
    await get_redis().flushdb()
    await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    await extend(db, BREAKOUT, 333)
    await run_analytics(db, SETTINGS, through=DAYS[334])
    assert await snapshot(db) == with_later

"""Signal performance by hand: win rate, average gain and loss, expectancy in R (a stop hit
counts -1R), stop hits and days to +20%, by type, grade bucket and regime; and the endpoint
over the signal log."""

from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import sessions_between
from app.data.universe import plan_universe, sync_tickers
from app.intraday.store import ticker_ids
from app.models import Signal, SignalOutcome
from app.scanner.performance import SignalRow, r_multiple, stats, summarize
from tests.test_universe import listed

D = date(2026, 3, 2)


def row(
    kind: str = "breakout",
    ret20: float | None = 10.0,
    *,
    grade: str | None = "A",
    regime: str | None = "confirmed_uptrend",
    stop_after: int | None = None,
    gain_after: int | None = None,
    observed: int = 60,
    scored: bool = True,
) -> SignalRow:
    # Close 101 on the signal day, plan entry 100.10, stop 96.10: 1R = 4.00.
    return SignalRow(
        type=kind,
        date=D,
        grade=grade,
        regime=regime,
        price=101.0,
        entry=100.10,
        stop=96.10,
        returns={5: 2.0, 10: 4.0, 20: ret20, 60: None},
        observed=observed,
        stop_hit_after=stop_after,
        gain_20_after=gain_after,
        scored=scored,
    )


def test_r_multiple_by_hand() -> None:
    # +10% from 101 = 111.10: (111.10 - 100.10) / 4.00 = 2.75R.
    assert r_multiple(row(ret20=10.0), 20) == pytest.approx(2.75)
    # The stop hit on session 12 counts -1R at 20 sessions, not at 10.
    assert r_multiple(row(stop_after=12), 20) == -1.0
    assert r_multiple(row(stop_after=12), 10) == pytest.approx((101 * 1.04 - 100.10) / 4)
    assert r_multiple(row(kind="near_pivot"), 20) is None  # fires before the entry


def test_stats_by_hand() -> None:
    rows = [
        row(ret20=10.0, gain_after=8),
        row(ret20=-5.0, stop_after=3),
        row(ret20=0.0),
        row(ret20=None, observed=12),  # too young for 20 sessions
    ]
    s = stats(rows, 20)
    assert (s["signals"], s["measured"]) == (4, 3)
    assert s["win_rate_pct"] == 33.3  # one of three closes higher
    assert (s["avg_gain_pct"], s["avg_loss_pct"], s["avg_return_pct"]) == (10.0, -2.5, 1.67)
    # R: +2.75, -1 (stopped), (101 - 100.10) / 4 = +0.225; the young one isn't measured.
    assert s["expectancy_r"] == pytest.approx((2.75 - 1 + 0.225) / 3, abs=0.005)
    assert s["r_count"] == 3
    # Stop: hit by 1 of the 3 old enough (the young one had no hit yet).
    assert s["stop_hit_pct"] == 33.3
    assert (s["reached_20_pct"], s["median_days_to_20"]) == (25.0, 8)


def test_summary_groups_by_type_bucket_and_regime() -> None:
    rows = [
        row(grade="A+"),
        row(grade="A"),
        row(grade=None, regime="correction", ret20=-3.0),
        row("pocket_pivot", grade="B"),
        # No Setup Score at all (no setup on the stock): not "below C".
        row("pocket_pivot", grade=None, scored=False),
    ]
    out = summarize(rows, 20)
    breakout, pivots = out["types"]
    assert (breakout["type"], breakout["label"], breakout["r"]) == (
        "breakout",
        "Breakout confirmed",
        True,
    )
    assert [b["bucket"] for b in breakout["buckets"]] == ["A+", "A", "Below C"]
    assert [b["bucket"] for b in pivots["buckets"]] == ["B", "Not scored"]
    assert [(g["regime"], g["signals"]) for g in breakout["regimes"]] == [
        ("confirmed_uptrend", 2),
        ("correction", 1),
    ]
    assert pivots["all"]["expectancy_r"] is None
    assert out["total"]["signals"] == 5


@pytest.mark.integration
async def test_the_endpoint_reads_the_signal_log(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT")), D)
    spot = (await ticker_ids(db, ["SPOT"]))["SPOT"]
    days = sessions_between(D, date(2026, 6, 30))
    signals = [
        (days[0], "breakout", "A", 101.0, 100.10, 96.10),
        (days[1], "near_pivot", "B", 99.0, 100.10, 96.10),
        (days[2], "breakout", "A", 101.0, 100.10, 96.10),
    ]
    ids = []
    for day, kind, grade, price, entry, stop in signals:
        result = await db.execute(
            insert(Signal)
            .values(
                date=day,
                type=kind,
                ticker_id=spot,
                summary=kind,
                price=price,
                entry=entry,
                stop=stop,
                score=85.0,
                grade=grade,
                context={"market": {"state": "confirmed_uptrend"}},
            )
            .returning(Signal.id)
        )
        ids.append(result.scalar_one())
    outcomes: list[dict[str, Any]] = [
        # Up 10% at 20 sessions; +20% reached on the 8th session after the signal.
        {"ret_20": 10.0, "gain_20_on": days[8], "stop_hit_on": None},
        {"ret_20": -2.0, "gain_20_on": None, "stop_hit_on": None},
        # Stopped on the 3rd session after its signal.
        {"ret_20": -6.0, "gain_20_on": None, "stop_hit_on": days[5]},
    ]
    # A market regime change has no stock and no Setup Score.
    await db.execute(
        insert(Signal).values(
            date=days[1],
            type="regime_change",
            ticker_id=None,
            summary="regime_change",
            context={"market": {"state": "confirmed_uptrend"}},
        )
    )
    await db.execute(
        insert(SignalOutcome),
        [
            {"signal_id": i, "sessions_observed": 60, "complete": True, **o}
            for i, o in zip(ids, outcomes, strict=True)
        ],
    )
    await db.commit()
    body = (await signed_in.get("/api/performance")).json()
    assert (body["horizon"], body["first"], body["last"]) == (
        20,
        days[0].isoformat(),
        days[2].isoformat(),
    )
    breakout = next(t for t in body["types"] if t["type"] == "breakout")
    assert breakout["all"]["signals"] == 2
    assert breakout["all"]["win_rate_pct"] == 50.0
    assert breakout["all"]["expectancy_r"] == 0.88  # (+2.75R - 1R) / 2, to 2 decimals
    assert breakout["all"]["median_days_to_20"] == 8
    assert breakout["all"]["stop_hit_pct"] == 50.0
    assert [b["bucket"] for b in breakout["buckets"]] == ["A"]
    regime = next(t for t in body["types"] if t["type"] == "regime_change")
    assert [b["bucket"] for b in regime["buckets"]] == ["Not scored"]
    later = (await signed_in.get("/api/performance", params={"since": days[2].isoformat()})).json()
    assert later["total"]["signals"] == 1
    bad = await signed_in.get("/api/performance", params={"horizon": 7})
    assert bad.status_code == 422

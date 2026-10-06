"""The backtest lab end to end on the synthetic market: start a run over the API, let the job
run it (as the worker would), read the report; a second run reuses the tape; the sensitivity
grid fills the heatmap; one run at a time; trade charts; saved screens narrow the trades."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.jobs import run_backtest
from app.core.config import get_settings
from app.models import BacktestRun, BacktestTape, SavedScreen, User
from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.backtest_market import LAST, seed_market
from tests.test_detection_pipeline import DAYS

FIRST = 300
ANY_GRADE = {"min_grade": None}


@pytest.fixture
def tape_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "backtest_dir", str(tmp_path))
    return tmp_path


async def start(client: httpx.AsyncClient, **body: object) -> dict[str, Any]:
    payload = {"start": DAYS[FIRST].isoformat(), "end": DAYS[LAST].isoformat(), **body}
    response = await client.post("/api/backtests", json=payload)
    assert response.status_code == 202, response.text
    run: dict[str, Any] = response.json()
    return run


@pytest.mark.integration
async def test_a_backtest_runs_and_reports(
    db: AsyncSession, signed_in: httpx.AsyncClient, user: User, tape_dir: Path
) -> None:
    await seed_market(db)
    await run_analytics(db, AppSettings(), through=DAYS[LAST])

    options = (await signed_in.get("/api/backtests/options")).json()
    assert options["last_date"] == DAYS[LAST].isoformat()
    assert options["defaults"]["portfolio"] == {
        "initial_capital": 100_000.0,
        "risk_pct": 1.0,
        "max_position_pct": 25.0,
        "max_positions": 10,
        "slippage_pct": 0.1,
        "commission": 0.0,
    }
    assert options["defaults"]["exits"]["trailing"] == "sma50"
    assert options["grid"] == {"vcp": [6, 8, 10, 12, 14], "volume": [100, 120, 140, 160, 180, 200]}

    run = await start(signed_in, rules=ANY_GRADE)
    assert run["status"] == "queued"
    assert run["name"] == f"Setups graded any grade, {DAYS[FIRST]} to {DAYS[LAST]}"
    busy = await signed_in.post("/api/backtests", json={"rules": ANY_GRADE})
    assert busy.status_code == 409
    assert "already running" in busy.json()["detail"]

    await run_backtest(int(run["id"]))  # what the worker does
    detail = (await signed_in.get(f"/api/backtests/{run['id']}")).json()
    assert detail["status"] == "done"
    report = detail["report"]
    assert report["hypothetical"] is True
    assert "Survivorship bias" in report["labels"]["survivorship"]
    assert len(report["equity"]["dates"]) == LAST - FIRST + 1
    assert len(report["equity"]["benchmark"]) == len(report["equity"]["dates"])
    assert report["tape"]["reused"] is False
    assert report["signals"]["breakout"] >= 3
    trades = detail["trades"]
    symbols = {t["symbol"] for t in trades}
    # SPOT, A and O break out of their VCPs together; GOOG's handle widened its stop past
    # the 8% maximum ("risk too wide"), which the default rules skip.
    assert symbols == {"SPOT", "A", "O"}
    by = {t["symbol"]: t for t in trades}
    spot = by["SPOT"]
    assert spot["entry_date"] == DAYS[333].isoformat()  # the buy-stop fills on the breakout
    assert spot["pnl"] > 0
    assert spot["exit_reason"] == "Closed below the 50-day SMA"
    assert (by["A"]["exit_reason"], by["A"]["r"]) == ("Stop", pytest.approx(-1, abs=0.05))
    assert report["summary"]["trades"] == len(trades)
    assert detail["summary"]["trades"] == len(trades)
    assert {r["key"] for r in report["by_pattern"]} >= {"vcp"}
    assert report["samples"]["in"]["end"] == report["samples"]["out"]["start"]

    chart = (await signed_in.get(f"/api/backtests/{run['id']}/trades/{spot['n']}/chart")).json()
    assert chart["symbol"] == "SPOT"
    assert DAYS[333].isoformat() in chart["time"]
    assert chart["trade"]["entry_price"] == spot["entry_price"]
    missing = await signed_in.get(f"/api/backtests/{run['id']}/trades/999/chart")
    assert missing.status_code == 404

    # Other rules and exits on the same dates and settings: the tape is reused. Allowing wide
    # stops trades GOOG's breakout too.
    rules = {**ANY_GRADE, "skip_risk_too_wide": False}
    again = await start(signed_in, rules=rules, exits={"trailing": "ema21"})
    await run_backtest(int(again["id"]))
    second = (await signed_in.get(f"/api/backtests/{again['id']}")).json()
    assert "GOOG" in {t["symbol"] for t in second["trades"]}
    assert second["report"]["tape"]["reused"] is True
    assert second["report"]["tape"]["id"] == report["tape"]["id"]
    assert len((await db.scalars(select(BacktestTape))).all()) == 1

    # A saved screen that no candidate matches: no trades.
    screen = SavedScreen(
        user_id=user.id, name="Huge RS", filters=[{"field": "rs_rating", "min": 100}]
    )
    db.add(screen)
    await db.commit()
    screened = await start(signed_in, rules=ANY_GRADE, screen_id=screen.id)
    assert screened["screen"] == "Huge RS"
    await run_backtest(int(screened["id"]))
    third = (await signed_in.get(f"/api/backtests/{screened['id']}")).json()
    assert third["trades"] == []
    assert third["name"].endswith(f"matching “Huge RS”, {DAYS[FIRST]} to {DAYS[LAST]}")

    listing = (await signed_in.get("/api/backtests")).json()
    assert [r["id"] for r in listing] == [screened["id"], again["id"], run["id"]]
    assert (await signed_in.delete(f"/api/backtests/{again['id']}")).status_code == 204
    assert (await signed_in.get(f"/api/backtests/{again['id']}")).status_code == 404


@pytest.mark.integration
async def test_the_sensitivity_grid_fills_the_heatmap(
    db: AsyncSession, signed_in: httpx.AsyncClient, tape_dir: Path
) -> None:
    await seed_market(db)
    await run_analytics(db, AppSettings(), through=DAYS[LAST])
    run = await start(signed_in, rules=ANY_GRADE, sensitivity=True)
    await run_backtest(int(run["id"]))
    report = (await signed_in.get(f"/api/backtests/{run['id']}")).json()["report"]
    heatmap = report["heatmap"]
    assert heatmap["base"] == {"vcp": 10, "volume": 140}
    assert len(heatmap["cells"]) == 5
    assert all(len(row) == 6 and all(c is not None for c in row) for row in heatmap["cells"])
    # The run's own cell (VCP 10%, volume 140%) is the main result.
    own = heatmap["cells"][2][2]
    assert own["trades"] == report["summary"]["trades"]
    # A's breakout (~151% of average volume) is confirmed at 140% and held into its stop
    # (-1R); at 160% it isn't, so it's sold at the entry day's close: a better result.
    above = heatmap["cells"][2][3]
    assert above["trades"] == own["trades"]
    assert above["expectancy_r"] > own["expectancy_r"]
    # At a 6% VCP limit the bases don't qualify while their third contraction (7%) is the
    # latest: the setups that briefly tracked them end, and the pipeline never tracks an
    # ended setup's pattern again, so the 4% fourth contraction comes too late. No trades
    # at 6%; from 8% up the same three.
    assert heatmap["cells"][0][2]["trades"] == 0
    assert all(row[2] == own for row in heatmap["cells"][1:])
    tape = await db.scalar(select(BacktestTape))
    assert tape is not None
    assert len(tape.cells) == 30
    assert (await db.scalar(select(BacktestRun.status))) == "done"

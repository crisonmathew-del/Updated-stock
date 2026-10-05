"""Setups and signals API on the drawn VCP breakout from tests.test_setups_pipeline."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.test_detection_pipeline import DAYS
from tests.test_patterns import VCP
from tests.test_setups_pipeline import BREAKOUT, extend, seed

SETTINGS = AppSettings()


async def breakout_market(db: AsyncSession) -> None:
    await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    await extend(db, BREAKOUT, 333)
    await run_analytics(db, SETTINGS, through=DAYS[334])


@pytest.mark.integration
async def test_setups_list_detail_and_stock_lookup(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    await breakout_market(db)
    response = await signed_in.get("/api/setups")
    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == DAYS[334].isoformat()
    spot = next(s for s in body["items"] if s["symbol"] == "SPOT")
    assert (spot["state"], spot["state_label"], spot["pattern_label"]) == (
        "breakout",
        "Breakout",
        "Volatility contraction (VCP)",
    )
    assert (spot["entry"], spot["stop"], spot["breakout_date"]) == (
        92.56,
        88.71,
        DAYS[333].isoformat(),
    )
    assert body["counts"]["breakout"] >= 1
    only = (await signed_in.get("/api/setups", params={"state": "breakout"})).json()
    assert {s["state"] for s in only["items"]} == {"breakout"}

    detail = (await signed_in.get(f"/api/setups/{spot['id']}")).json()
    assert len(detail["components"]) == 6
    assert [(t["to_state"], t["to_label"]) for t in detail["transitions"]] == [
        ("near_pivot", "Near pivot"),
        ("breakout", "Breakout"),
    ]
    assert detail["trade_plan"]["target_2r"] == 100.26
    assert detail["pattern"]["type"] == "vcp"
    by_type = {s["type"]: s for s in detail["signals"]}
    assert by_type["breakout"]["type_label"] == "Breakout confirmed"

    lookup = (await signed_in.get("/api/stocks/spot/setup")).json()
    assert lookup["id"] == spot["id"]
    assert (await signed_in.get("/api/stocks/NOPE/setup")).json() is None
    missing = await signed_in.get("/api/setups/999999")
    assert (missing.status_code, missing.json()["detail"]) == (404, "No setup 999999.")


@pytest.mark.integration
async def test_signal_log_with_outcomes(db: AsyncSession, signed_in: httpx.AsyncClient) -> None:
    await breakout_market(db)
    body = (await signed_in.get("/api/signals", params={"symbol": "SPOT"})).json()
    assert body["counts"]["near_pivot"] == 1
    assert body["counts"]["breakout"] == 1
    assert [s["date"] for s in body["items"]] == sorted(
        (s["date"] for s in body["items"]), reverse=True
    )
    near = next(s for s in body["items"] if s["type"] == "near_pivot")
    outcome = near["outcome"]
    # Signal close 91.50 on session 332; session 333 closed at 92.75: +1.37%. No R: the price
    # hadn't reached the entry when a near-pivot signal fired.
    assert outcome["sessions_observed"] == 2
    assert outcome["returns"]["1"] == pytest.approx(1.37, abs=0.01)
    assert outcome["returns_r"]["1"] is None
    assert outcome["returns"]["5"] is None
    # The breakout closed 92.75 on session 333, then 94.00: in R with entry 92.56 and stop
    # 88.71, (94.00 - 92.56) / 3.85 = 0.37.
    breakout = next(s for s in body["items"] if s["type"] == "breakout")
    assert breakout["outcome"]["returns_r"]["1"] == 0.37
    filtered = (await signed_in.get("/api/signals", params={"type": "breakout"})).json()
    assert {s["type"] for s in filtered["items"]} == {"breakout"}


@pytest.mark.integration
async def test_setups_need_a_session(client: httpx.AsyncClient, db: AsyncSession) -> None:
    assert (await client.get("/api/setups")).status_code == 401
    assert (await client.get("/api/signals")).status_code == 401

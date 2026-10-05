"""Pattern review API and the per-stock fundamentals/patterns endpoints."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FundamentalsQuarterly
from app.scanner.eod_scan import run_analytics
from tests.test_detection_pipeline import DAYS, SETTINGS, seed
from tests.test_patterns import VCP

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_redis")]


async def scanned(db: AsyncSession) -> dict[str, int]:
    ids = await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[332])
    return ids


async def test_review_endpoints_need_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/admin/patterns")).status_code == 401
    assert (await client.get("/api/stocks/SPOT/fundamentals")).status_code == 401


async def test_list_review_and_stats(db: AsyncSession, signed_in: httpx.AsyncClient) -> None:
    await scanned(db)
    listed = (await signed_in.get("/api/admin/patterns", params={"type": "vcp"})).json()
    [vcp] = listed
    assert (vcp["symbol"], vcp["type_label"], vcp["status"]) == (
        "SPOT",
        "Volatility contraction (VCP)",
        "forming",
    )
    assert vcp["review"] is None

    chart = await signed_in.get(f"/api/admin/patterns/{vcp['id']}/chart.png")
    assert chart.status_code == 200
    assert chart.headers["content-type"] == "image/png"
    assert chart.content.startswith(b"\x89PNG")
    dark = await signed_in.get(f"/api/admin/patterns/{vcp['id']}/chart.png?theme=dark")
    assert dark.content != chart.content

    reviewed = await signed_in.put(
        f"/api/admin/patterns/{vcp['id']}/review",
        json={"verdict": "wrong", "note": "Contraction 2 looks loose."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review"]["verdict"] == "wrong"
    again = await signed_in.put(
        f"/api/admin/patterns/{vcp['id']}/review", json={"verdict": "correct"}
    )
    assert again.json()["review"] == {**again.json()["review"], "verdict": "correct", "note": None}

    stats = (await signed_in.get("/api/admin/patterns/review-stats")).json()
    by_type = {t["type"]: t for t in stats["types"]}
    assert by_type["vcp"]["reviewed"] == 1
    assert by_type["vcp"]["correct"] == 1
    assert by_type["vcp"]["false_positive_rate"] == 0.0
    assert stats["reviewed"] == 1

    cleared = await signed_in.delete(f"/api/admin/patterns/{vcp['id']}/review")
    assert cleared.status_code == 204
    stats = (await signed_in.get("/api/admin/patterns/review-stats")).json()
    assert stats["reviewed"] == 0
    assert stats["false_positive_rate"] is None

    bad = await signed_in.put(f"/api/admin/patterns/{vcp['id']}/review", json={"verdict": "maybe"})
    assert bad.status_code == 422
    missing = await signed_in.get("/api/admin/patterns/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "No detection with id 999999."


async def test_sample_is_seeded_and_spread_across_types(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    await scanned(db)
    everything = (await signed_in.get("/api/admin/patterns", params={"limit": 500})).json()
    types = {p["type"] for p in everything}
    first = (
        await signed_in.get("/api/admin/patterns/sample", params={"size": 20, "seed": 7})
    ).json()
    second = (
        await signed_in.get("/api/admin/patterns/sample", params={"size": 20, "seed": 7})
    ).json()
    assert [p["id"] for p in first] == [p["id"] for p in second]
    assert len(first) == min(20, len(everything))
    # The first len(types) picks are one of each type.
    assert {p["type"] for p in first[: len(types)]} == types

    await signed_in.put(f"/api/admin/patterns/{first[0]['id']}/review", json={"verdict": "unsure"})
    fresh = (
        await signed_in.get(
            "/api/admin/patterns/sample", params={"size": 20, "seed": 7, "unreviewed": True}
        )
    ).json()
    assert first[0]["id"] not in {p["id"] for p in fresh}


async def test_stock_patterns_and_fundamentals(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    ids = await scanned(db)
    patterns = (await signed_in.get("/api/stocks/spot/patterns")).json()
    assert "vcp" in {p["type"] for p in patterns}

    day = DAYS[332]
    rows = [
        (day - timedelta(days=405), 0.50, 100e6),
        (day - timedelta(days=313), 0.55, 104e6),
        (day - timedelta(days=131), 0.70, 120e6),
        (day - timedelta(days=40), 0.80, 130e6),
    ]
    await db.execute(
        insert(FundamentalsQuarterly),
        [
            {
                "ticker_id": ids["AAPL"],
                "period_end": end,
                "reported_date": end + timedelta(days=30),
                "period_start": end - timedelta(days=90),
                "fiscal_year": 2025,
                "fiscal_period": f"Q{i + 1}",
                "eps_diluted": eps,
                "revenue": revenue,
                "source": "test",
            }
            for i, (end, eps, revenue) in enumerate(rows)
        ],
    )
    await db.commit()
    body = (await signed_in.get("/api/stocks/AAPL/fundamentals")).json()
    assert body["as_of"] == day.isoformat()
    assert body["grade"]["basis"] == "quarterly"
    keys = [c["key"] for c in body["grade"]["components"]]
    assert keys[0] == "eps_growth"
    latest = body["quarters"][0]
    assert latest["label"] == "Q4 FY2025"
    assert latest["eps_growth_pct"] == 60.0  # 0.80 vs 0.50 a year earlier
    assert latest["revenue_growth_pct"] == 30.0
    assert body["quarters"][-1]["eps_growth_pct"] is None  # no year-earlier quarter
    # The latest quarter was filed 10 days before the scan date; 15 days before, it was unknown.
    earlier = (
        await signed_in.get(
            "/api/stocks/AAPL/fundamentals", params={"on": (day - timedelta(days=15)).isoformat()}
        )
    ).json()
    assert earlier["quarters"][0]["label"] == "Q3 FY2025"

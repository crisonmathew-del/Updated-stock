import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ticker
from app.scanner.eod_scan import run_analytics
from tests.test_eod_scan import END, SETTINGS, seed_market

PATHS = (
    "/api/market/regime",
    "/api/market/breadth",
    "/api/groups",
    "/api/stocks/AAPL",
    "/api/stocks/AAPL/indicators",
)


SOFTWARE = ("AAPL", "SPOT", "TSM")


@pytest.fixture
async def analysed(db: AsyncSession, signed_in: httpx.AsyncClient) -> httpx.AsyncClient:
    await seed_market(db)
    # Three stocks share SIC 7372, below the 5-member threshold, so they roll up into one
    # rankable "Business Services (other)" group; the rest stay alone and unranked.
    await db.execute(
        update(Ticker)
        .where(Ticker.symbol.in_(SOFTWARE))
        .values(sic_code="7372", sic_description="Services-Prepackaged Software")
    )
    await db.commit()
    await run_analytics(db, SETTINGS, through=END)
    return signed_in


@pytest.mark.integration
@pytest.mark.usefixtures("db", "clean_redis")
async def test_market_and_stock_routes_need_a_session(client: httpx.AsyncClient) -> None:
    for path in PATHS:
        assert (await client.get(path)).status_code == 401, path


@pytest.mark.integration
async def test_empty_database_gives_empty_answers(signed_in: httpx.AsyncClient) -> None:
    regime = (await signed_in.get("/api/market/regime")).json()
    assert regime["state"] is None
    assert regime["indexes"] == []
    groups = (await signed_in.get("/api/groups")).json()
    assert groups == {"date": None, "groups": [], "sectors": []}


@pytest.mark.integration
async def test_regime_with_reasons_and_history(analysed: httpx.AsyncClient) -> None:
    body = (await analysed.get("/api/market/regime?days=10")).json()
    assert body["date"] == END.isoformat()
    assert body["label"] == "Confirmed uptrend"
    assert [i["symbol"] for i in body["indexes"]] == ["IWM", "QQQ", "SPY"]
    spy = body["indexes"][2]
    assert spy["reasons"]
    assert any("50-day SMA" in r for r in spy["reasons"])
    assert len(body["history"]) == 10
    assert set(body["history"][0]["states"]) == {"IWM", "QQQ", "SPY", "MARKET"}


@pytest.mark.integration
async def test_breadth_newest_first(analysed: httpx.AsyncClient) -> None:
    rows = (await analysed.get("/api/market/breadth?days=5")).json()
    assert len(rows) == 5
    assert rows[0]["date"] == END.isoformat()
    assert rows[0]["members"] == 8


@pytest.mark.integration
async def test_groups_and_sector_rotation(analysed: httpx.AsyncClient) -> None:
    body = (await analysed.get("/api/groups")).json()
    assert body["date"] == END.isoformat()
    [group] = body["groups"]  # only groups with 3+ members are ranked
    assert (group["rank"], group["name"], group["members"]) == (1, "Business Services", 3)
    assert group["sector"] == "Industrials"
    assert group["median_rs"] is not None
    sectors = body["sectors"]
    assert len(sectors) == 11
    assert [s["rank"] for s in sectors] == list(range(1, 12))
    assert {s["sector"] for s in sectors} >= {"Information Technology", "Energy", "Real Estate"}
    assert all(s["rank_change_4w"] is not None for s in sectors)


@pytest.mark.integration
async def test_stock_summary_explains_the_trend_template(analysed: httpx.AsyncClient) -> None:
    body = (await analysed.get("/api/stocks/aapl")).json()
    assert body["symbol"] == "AAPL"
    assert body["date"] == END.isoformat()
    assert body["stage_label"] == "Stage 2 · advancing"
    assert body["sector"] == "Information Technology"  # 7372 is software
    assert body["group"]["name"] == "Business Services"
    assert body["group"]["rank"] == 1
    assert len(body["checks"]) == 8
    assert body["trend_template_passed"] == sum(c["passed"] for c in body["checks"])
    above_50 = next(c for c in body["checks"] if c["key"] == "above_50")
    assert above_50["detail"].startswith("Close ")
    assert body["indicators"]["sma50"] is not None


@pytest.mark.integration
async def test_unknown_symbol_is_a_clear_404(analysed: httpx.AsyncClient) -> None:
    response = await analysed.get("/api/stocks/NOPE")
    assert response.status_code == 404
    assert response.json() == {"detail": "No ticker NOPE in the universe."}


@pytest.mark.integration
async def test_indicator_history(analysed: httpx.AsyncClient) -> None:
    rows = (await analysed.get("/api/stocks/AAPL/indicators?days=30")).json()
    assert len(rows) == 30
    assert rows[-1]["date"] == END.isoformat()
    assert {"close", "sma50", "rs_rating", "stage"} <= set(rows[-1])

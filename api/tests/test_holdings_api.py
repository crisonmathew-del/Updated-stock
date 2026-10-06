"""Holdings: entered by hand, priced live or at the close, P&L in R with the sell rules.

Position: entry 100.00, initial stop 92.00 (8.00 a share of risk), 50 shares.
"""

import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import MARKET_TZ
from app.core.redis import get_redis
from app.data.bars import upsert_bars
from app.data.universe import plan_universe, sync_tickers
from app.intraday.live import QUOTES_KEY, REFRESH_CHANNEL
from app.intraday.store import ticker_ids
from app.models import IndicatorDaily
from app.providers.base import Bar, PriceHistory
from tests.test_universe import listed

DAY = date(2026, 10, 1)


@pytest.mark.integration
async def test_holdings_lifecycle(db: AsyncSession, signed_in: httpx.AsyncClient) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT")), DAY)
    ids = await ticker_ids(db, ["SPOT"])
    history = PriceHistory(
        "SPOT",
        [Bar(DAY - timedelta(days=1), 108, 110, 107, 110.0, 1), Bar(DAY, 110, 113, 109, 112.0, 1)],
    )
    await upsert_bars(db, {ids["SPOT"]: history}, "test")
    db.add(IndicatorDaily(ticker_id=ids["SPOT"], date=DAY, ema21=106.0, sma50=101.0))
    await db.commit()
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(REFRESH_CHANNEL)
    await pubsub.get_message(timeout=1)

    bad = await signed_in.post(
        "/api/holdings",
        json={"symbol": "SPOT", "entry_price": 100, "shares": 50, "initial_stop": 101},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"] == "The stop must be below the entry price (long positions only)."

    created = await signed_in.post(
        "/api/holdings",
        json={
            "symbol": "spot",
            "entry_price": 100,
            "shares": 50,
            "initial_stop": 92,
            "opened_on": "2026-09-15",
        },
    )
    assert created.status_code == 201, created.text
    h = created.json()
    # At the 112.00 close: +12.00 × 50 = +600, +12%, +1.5R; 2.00% on the day (110 → 112).
    assert (h["price"], h["price_source"], h["pnl"], h["pnl_pct"], h["r"]) == (
        112.0,
        "close",
        600.0,
        12.0,
        1.5,
    )
    assert h["day_change_pct"] == pytest.approx(1.82)
    assert (h["stop"], h["open_risk"], h["position_value"]) == (92.0, 1000.0, 5600.0)
    # +12% is past the 10% breakeven trigger and the stop is still under the entry.
    assert [w["rule"] for w in h["warnings"]] == ["breakeven"]
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert message is not None
    assert message["data"] == "holdings"

    # A live quote from today's session wins over the close.
    now = datetime.now(MARKET_TZ)
    quote = {"symbol": "SPOT", "last": 104.0, "prev_close": 112.0, "at": now.isoformat()}
    await get_redis().hset(QUOTES_KEY, "SPOT", json.dumps(quote))  # type: ignore[misc]
    [live] = (await signed_in.get("/api/holdings")).json()
    assert (live["price"], live["price_source"], live["r"]) == (104.0, "live", 0.5)
    # 104 is under the 21-day EMA (106) but above the 50-day (101).
    assert [w["rule"] for w in live["warnings"]] == ["below_21"]

    raised = await signed_in.patch(f"/api/holdings/{h['id']}", json={"stop": 100})
    assert raised.json()["stop"] == 100.0
    assert raised.json()["r"] == 0.5  # R stays measured against the initial stop

    closed = await signed_in.patch(
        f"/api/holdings/{h['id']}", json={"exit_price": 110, "closed_on": "2026-10-02"}
    )
    c = closed.json()
    assert (c["closed_on"], c["price"], c["price_source"], c["pnl"], c["r"]) == (
        "2026-10-02",
        110.0,
        "exit",
        500.0,
        1.25,
    )
    assert c["warnings"] == []
    assert (await signed_in.get("/api/holdings")).json() == []
    assert len((await signed_in.get("/api/holdings", params={"closed": True})).json()) == 1
    reopened = await signed_in.patch(f"/api/holdings/{h['id']}", json={"closed_on": None})
    assert reopened.json()["closed_on"] is None
    assert (await signed_in.delete(f"/api/holdings/{h['id']}")).status_code == 204
    assert (await signed_in.delete(f"/api/holdings/{h['id']}")).status_code == 404
    await pubsub.aclose()  # type: ignore[no-untyped-call]

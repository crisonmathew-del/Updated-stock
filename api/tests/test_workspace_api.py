"""Phase 5 API: search, chart, screener snapshot, quotes, peers, notes, saved screens and
watchlists, on the drawn VCP breakout market (SPOT broke out on session 333)."""

import base64

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.scanner.eod_scan import run_analytics
from app.scanner.screener_rows import FIELDS, SPARK_POINTS
from app.settings.schema import AppSettings
from tests.test_detection_pipeline import DAYS
from tests.test_setups_api import breakout_market


@pytest.mark.integration
async def test_read_endpoints_for_the_stock_page_and_screener(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    await breakout_market(db)

    # Search: exact symbol first, lower-case prefixes, company names, then fuzzy.
    hits = (await signed_in.get("/api/search", params={"q": "spot"})).json()
    assert hits[0]["symbol"] == "SPOT"
    assert hits[0]["close"] == pytest.approx(94.0)
    assert hits[0]["state"] == "breakout"
    assert hits[0]["change_pct"] == pytest.approx((94.0 / 92.75 - 1) * 100, abs=0.01)
    lower = (await signed_in.get("/api/search?q=aap")).json()
    assert lower[0]["symbol"] == "AAPL"
    named = (await signed_in.get("/api/search", params={"q": "spotify"})).json()
    assert named[0]["symbol"] == "SPOT"
    assert (await signed_in.get("/api/search", params={"q": "zzzzqqq"})).json() == []

    # Summary: the header's numbers.
    summary = (await signed_in.get("/api/stocks/SPOT")).json()
    assert (summary["close"], summary["prev_close"]) == (pytest.approx(94.0), pytest.approx(92.75))
    assert summary["change_pct"] == pytest.approx(1.35, abs=0.01)
    assert summary["volume_ratio"] is not None

    # Chart: aligned series, the moving averages, the overlay with the plan, markers.
    chart = (await signed_in.get("/api/stocks/SPOT/chart", params={"sessions": 300})).json()
    series = chart["series"]
    assert len(series["time"]) == 300
    assert series["time"][-1] == DAYS[334].isoformat()
    assert set(series["ma"]) == {"ema10", "ema21", "sma50", "sma150", "sma200"}
    assert all(len(v) == 300 for v in (series["close"], series["volume"], series["rs_line"]))
    overlay = chart["overlay"]
    assert (overlay["type"], overlay["setup_state"]) == ("vcp", "breakout")
    assert (overlay["pivot"], overlay["entry"], overlay["stop"]) == (92.46, 92.56, 88.71)
    assert overlay["buy_zone_top"] == pytest.approx(92.46 * 1.05, abs=1e-4)
    assert [c["number"] for c in overlay["contractions"]] == [1, 2, 3, 4]
    assert [round(c["depth_pct"], 1) for c in overlay["contractions"]] == [24.8, 13.9, 7.0, 4.0]
    signals = [m for m in chart["markers"] if m["kind"] == "signal"]
    assert {m["time"] for m in signals} >= {DAYS[332].isoformat(), DAYS[333].isoformat()}
    weekly = (await signed_in.get("/api/stocks/SPOT/chart", params={"timeframe": "weekly"})).json()
    assert set(weekly["series"]["ma"]) == {"sma10w", "sma30w", "sma40w"}
    # Only 335 sessions are seeded: one candle per ISO week of them.
    weeks = {d.isocalendar()[:2] for d in DAYS[:335]}
    assert len(weekly["series"]["time"]) == len(weeks)
    assert weekly["overlay"]["start"] in weekly["series"]["time"]
    missing = await signed_in.get("/api/stocks/NOPE/chart")
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "No ticker NOPE in the universe.",
    )

    # Screener snapshot: one row per stock, columnar, cached.
    first = await signed_in.get("/api/screener")
    body = first.json()
    assert body["as_of"] == DAYS[334].isoformat()
    assert body["fields"] == list(FIELDS)
    rows = {r[0]: dict(zip(body["fields"], r, strict=True)) for r in body["rows"]}
    spot = rows["SPOT"]
    assert (spot["setup_state"], spot["pattern"], spot["breakout_today"]) == (
        "breakout",
        "vcp",
        False,  # it broke out the session before
    )
    assert spot["close"] == pytest.approx(94.0)
    assert "SPY" not in rows  # benchmarks aren't screened
    assert 2 <= len(base64.b64decode(spot["spark"])) <= SPARK_POINTS
    again = await signed_in.get("/api/screener")
    assert again.content == first.content

    # Top bar quotes and group peers.
    quotes = (await signed_in.get("/api/market/quotes")).json()
    assert [q["symbol"] for q in quotes] == ["SPY", "QQQ", "IWM"]
    assert quotes[0]["close"] is not None
    # Peers need an industry group: give both stocks the same SIC code and re-run the session.
    await db.execute(
        text(
            "UPDATE tickers SET sic_code = '7372', sic_description = 'Software' "
            "WHERE symbol IN ('SPOT', 'AAPL')"
        )
    )
    await db.commit()
    await run_analytics(db, AppSettings(), through=DAYS[334])
    peers = (await signed_in.get("/api/stocks/SPOT/peers")).json()
    assert {p["symbol"] for p in peers} == {"SPOT", "AAPL"}
    assert [p["symbol"] for p in peers if p["is_self"]] == ["SPOT"]


@pytest.mark.integration
async def test_watchlists_screens_and_notes(db: AsyncSession, signed_in: httpx.AsyncClient) -> None:
    await breakout_market(db)

    # The `w` shortcut creates "Watchlist" on first use; adding twice is a no-op.
    first = (await signed_in.post("/api/watchlists/default/items", json={"symbol": "spot"})).json()
    assert (first["name"], [i["symbol"] for i in first["items"]]) == ("Watchlist", ["SPOT"])
    wid = first["id"]
    await signed_in.post("/api/watchlists/default/items", json={"symbol": "SPOT"})
    added = (
        await signed_in.post(f"/api/watchlists/{wid}/items", json={"symbol": "AAPL", "note": "x"})
    ).json()
    assert [i["symbol"] for i in added["items"]] == ["SPOT", "AAPL"]
    assert added["items"][0]["state"] == "breakout"
    reordered = (
        await signed_in.put(f"/api/watchlists/{wid}/order", json={"symbols": ["AAPL", "SPOT"]})
    ).json()
    assert [i["symbol"] for i in reordered["items"]] == ["AAPL", "SPOT"]
    noted = (
        await signed_in.patch(f"/api/watchlists/{wid}/items/SPOT", json={"note": "VCP, 4T"})
    ).json()
    assert next(i["note"] for i in noted["items"] if i["symbol"] == "SPOT") == "VCP, 4T"
    member = (await signed_in.get("/api/stocks/SPOT/watchlists")).json()
    assert member == [{"id": wid, "name": "Watchlist", "contains": True}]
    second = await signed_in.post("/api/watchlists", json={"name": "Breakouts"})
    assert second.status_code == 201
    taken = await signed_in.post("/api/watchlists", json={"name": "Breakouts"})
    assert (taken.status_code, taken.json()["detail"]) == (
        409,
        'You already have a watchlist called "Breakouts".',
    )
    unknown = await signed_in.post(f"/api/watchlists/{wid}/items", json={"symbol": "NOPE"})
    assert unknown.status_code == 404
    removed = (await signed_in.delete(f"/api/watchlists/{wid}/items/AAPL")).json()
    assert [i["symbol"] for i in removed["items"]] == ["SPOT"]
    assert (await signed_in.delete(f"/api/watchlists/{second.json()['id']}")).status_code == 204
    assert [w["name"] for w in (await signed_in.get("/api/watchlists")).json()] == ["Watchlist"]

    # Saved screens: validated fields, unique names, partial updates.
    screen = {
        "name": "Leaders near pivot",
        "filters": [
            {"field": "tt_pass", "op": "is", "value": True},
            {"field": "readiness_pct", "op": "between", "min": 0, "max": 3},
            {"field": "grade", "op": "in", "values": ["A+", "A"]},
        ],
        "sort": {"field": "score", "desc": True},
        "columns": ["symbol", "grade", "score"],
    }
    created = await signed_in.post("/api/screens", json=screen)
    assert created.status_code == 201
    sid = created.json()["id"]
    assert (await signed_in.post("/api/screens", json=screen)).status_code == 409
    bad = await signed_in.post("/api/screens", json={**screen, "name": "x", "columns": ["nope"]})
    assert (bad.status_code, bad.json()["detail"]) == (422, "Unknown screener field(s): nope.")
    renamed = (await signed_in.patch(f"/api/screens/{sid}", json={"name": "Near pivot"})).json()
    assert (renamed["name"], len(renamed["filters"])) == ("Near pivot", 3)
    assert [s["name"] for s in (await signed_in.get("/api/screens")).json()] == ["Near pivot"]
    assert (await signed_in.delete(f"/api/screens/{sid}")).status_code == 204

    # Notes: saved per stock, an empty body deletes.
    assert (await signed_in.get("/api/stocks/SPOT/note")).json()["body"] == ""
    saved = (await signed_in.put("/api/stocks/SPOT/note", json={"body": "Watch the handle"})).json()
    assert saved["body"] == "Watch the handle"
    assert (await signed_in.get("/api/stocks/spot/note")).json()["body"] == "Watch the handle"
    cleared = (await signed_in.put("/api/stocks/SPOT/note", json={"body": "  "})).json()
    assert cleared == {"body": "", "updated_at": None}

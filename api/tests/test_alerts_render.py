"""What alert and digest emails say."""

from datetime import date, timedelta

from app.alerts.render import (
    CHART_CID,
    ChartBar,
    Digest,
    DigestSetup,
    alert_message,
    digest_message,
    facts,
    mini_chart,
)

ALERT = {
    "id": 1,
    "created_at": "2026-10-02T14:15:00+00:00",
    "session_date": "2026-10-02",
    "kind": "breakout_provisional",
    "priority": "high",
    "symbol": "SPOT",
    "title": "SPOT breaking out strongly (provisional)",
    "body": "92.80 is above the 92.46 pivot on projected volume of 200% of average (strong).",
    "payload": {
        "price": 92.80,
        "change_pct": 0.8695652,
        "volume_ratio_pct": 200.0,
        "partial_volume": True,
        "pivot": 92.46,
        "buy_zone_top": 97.08,
        "entry": 92.56,
        "stop": 88.71,
        "shares": 259,
        "grade": "A",
        "score": 84.2,
    },
    "delivery": {"in_app": "sent", "email": "queued"},
    "read": False,
}


def bars(n: int) -> list[ChartBar]:
    start = date(2026, 7, 1)
    return [
        ChartBar(start + timedelta(days=i), 90 + i * 0.05 + 1, 90 + i * 0.05 - 1, 90 + i * 0.05)
        for i in range(n)
    ]


def test_facts_show_the_numbers_that_matter() -> None:
    assert facts(ALERT["payload"]) == [  # type: ignore[arg-type]
        ("Price", "92.80 (+0.9%)"),
        ("Projected volume", "200% of 50-day average (IEX, scaled)"),
        ("Pivot", "92.46 (buy zone to 97.08)"),
        ("Plan", "entry 92.56, stop 88.71, 259 shares"),
        ("Setup", "A · 84/100"),
    ]
    assert facts({"price": 88.5, "r": -1.0545}) == [("Price", "88.50"), ("Open P&L", "-1.1R")]


def test_an_alert_email_links_to_the_stock_and_embeds_the_chart() -> None:
    message = alert_message(ALERT, "owner@example.com", "https://breakout.example/", b"png")
    assert message.subject == "SPOT breaking out strongly (provisional)"
    assert 'href="https://breakout.example/stocks/SPOT"' in message.html
    assert 'href="https://breakout.example/alerts"' in message.html
    assert f'src="cid:{CHART_CID}"' in message.html
    assert "▲ High priority · 2026-10-02" in message.html
    assert [i.cid for i in message.images] == [CHART_CID]
    assert "Open SPOT: https://breakout.example/stocks/SPOT" in message.text
    assert "Plan: entry 92.56, stop 88.71, 259 shares" in message.text
    assert "never places trades" in message.text
    # No chart (a market alert): no image and no broken reference.
    market = {**ALERT, "symbol": None, "payload": {}}
    plain = alert_message(market, "owner@example.com", "https://breakout.example", None)
    assert "cid:" not in plain.html
    assert plain.images == ()
    assert "/stocks/" not in plain.text


def test_text_is_escaped_in_html() -> None:
    message = alert_message(
        {**ALERT, "body": "Undercut & rally <test>"}, "o@example.com", "http://x", None
    )
    assert "Undercut &amp; rally &lt;test&gt;" in message.html


def test_mini_chart_is_a_png_and_needs_two_sessions() -> None:
    png = mini_chart(bars(60), last=93.5, pivot=92.46, buy_zone_top=97.08, stop=88.71)
    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert mini_chart(bars(1)) is None


def test_digest_lists_pending_alerts_and_top_setups() -> None:
    digest = Digest(
        kind="daily",
        title="Daily digest",
        period="Friday, October 2",
        regime="Market: Confirmed uptrend (2 distribution days, as of Oct 2).",
        alerts=[{**ALERT, "priority": "normal", "title": "AAPL is near its pivot", "body": "x"}],
        emailed=3,
        setups=[DigestSetup("SPOT", "A", 84.2, "near pivot", "VCP", 92.46, 1.2)],
        lines=[],
    )
    message = digest_message(digest, "owner@example.com", "https://breakout.example")
    assert message.subject == "Daily digest · Friday, October 2"
    assert "Alerts (1)" in message.text
    assert "- AAPL is near its pivot: x" in message.text
    assert "3 more alerts went out by email as they happened." in message.text
    assert "- SPOT A 84 · VCP · pivot 92.46 1.2% below pivot" in message.text
    assert "Market: Confirmed uptrend" in message.html
    empty = Digest("daily", "Daily digest", "Friday, October 2", None, [], 0, [], [])
    assert "No alerts." in digest_message(empty, "o@example.com", "http://x").text

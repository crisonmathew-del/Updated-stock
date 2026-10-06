"""The alerts centre's API: history, unread and read marks, test sends, delivery status and
alert rules (including a saved screen promoted to an alert)."""

from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import jobs as alert_jobs
from app.alerts.engine import AlertDraft, EmailRoute, raise_alerts
from app.api.routes import alerts as alerts_routes
from app.core.redis import get_redis
from app.data.universe import plan_universe, sync_tickers
from app.intraday.watcher import REFRESH_CHANNEL
from app.models import SavedScreen, User, Watchlist
from app.settings.schema import AppSettings
from tests.fakes import FakeEmailSender
from tests.test_universe import listed

DAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)


def draft(key: str, **values: object) -> AlertDraft:
    base: dict[str, object] = {
        "kind": "near_pivot",
        "priority": "normal",
        "title": f"{key} is near its pivot",
        "body": "1.2% below the pivot.",
        "session_date": DAY,
        "dedupe_key": key,
        "symbol": "SPOT",
        "grade": "A",
    }
    base.update(values)
    return AlertDraft(**base)  # type: ignore[arg-type]


@pytest.mark.integration
async def test_history_filters_pages_and_read_marks(
    db: AsyncSession, signed_in: httpx.AsyncClient
) -> None:
    drafts = [draft(f"a{i}") for i in range(3)] + [
        draft("b", kind="breakout", priority="high", symbol="AAPL", title="AAPL breakout")
    ]
    await raise_alerts(db, get_redis(), drafts, AppSettings(), NOW, EmailRoute(False, "x"))

    body = (await signed_in.get("/api/alerts", params={"limit": 2})).json()
    assert [a["title"] for a in body["items"]] == ["AAPL breakout", "a2 is near its pivot"]
    assert body["unread"] == 4
    assert body["items"][0]["kind_label"] == "Breakout confirmed"
    assert body["items"][0]["delivery"] == {"in_app": "sent", "email": "not configured: x"}
    assert {k["kind"]: k["count"] for k in body["kinds"]} == {"near_pivot": 3, "breakout": 1}
    page2 = (
        await signed_in.get("/api/alerts", params={"limit": 2, "before": body["next_before"]})
    ).json()
    assert [a["title"] for a in page2["items"]] == ["a1 is near its pivot", "a0 is near its pivot"]
    assert page2["next_before"] is None

    only = (await signed_in.get("/api/alerts", params={"priority": "high"})).json()
    assert [a["symbol"] for a in only["items"]] == ["AAPL"]
    kinds = (await signed_in.get("/api/alerts", params={"kind": "breakout,near_pivot"})).json()
    assert len(kinds["items"]) == 4
    by_symbol = (await signed_in.get("/api/alerts", params={"symbol": "aapl"})).json()
    assert len(by_symbol["items"]) == 1

    first = body["items"][0]["id"]
    assert (await signed_in.post("/api/alerts/read", json={"ids": [first]})).json() == {"count": 3}
    unread = (await signed_in.get("/api/alerts", params={"unread": True})).json()
    assert first not in [a["id"] for a in unread["items"]]
    assert (await signed_in.post("/api/alerts/read", json={})).json() == {"count": 0}
    assert (await signed_in.get("/api/alerts/unread")).json() == {"count": 0}


@pytest.mark.integration
async def test_test_sends_report_what_happened(
    signed_in: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    in_app = (await signed_in.post("/api/alerts/test", json={"channel": "in_app"})).json()
    assert in_app["kind"] == "test"
    assert in_app["delivery"] == {"in_app": "sent", "email": "off for this rule"}

    # No email provider here: the test says so.
    email = (await signed_in.post("/api/alerts/test", json={"channel": "email"})).json()
    assert email["delivery"]["email"].startswith("not configured: set RESEND_API_KEY")

    status = (await signed_in.get("/api/alerts/status")).json()
    assert status["email"]["configured"] is False
    assert status["streamer"]["alive"] is False
    assert status["streamer"]["state"] == "down"
    assert status["quiet_hours_now"] is False

    sender = FakeEmailSender()
    monkeypatch.setattr(alerts_routes, "email_route", lambda _: EmailRoute(True))
    monkeypatch.setattr(alert_jobs, "sender_from_config", lambda _: sender)
    sent = (await signed_in.post("/api/alerts/test", json={"channel": "email"})).json()
    assert sent["delivery"]["email"] == "sent"
    assert [m.subject for m in sender.sent] == ["Test alert (email)"]


@pytest.mark.integration
async def test_rules_are_validated_described_and_refresh_the_streamer(
    db: AsyncSession, user: User, signed_in: httpx.AsyncClient
) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT")), DAY)
    watchlist = Watchlist(user_id=user.id, name="Leaders")
    screen = SavedScreen(user_id=user.id, name="VCPs near pivot", filters=[])
    db.add_all([watchlist, screen])
    await db.commit()
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(REFRESH_CHANNEL)
    await pubsub.get_message(timeout=1)

    created = await signed_in.post(
        "/api/alert-rules",
        json={
            "name": "SPOT over 95",
            "scope": "ticker",
            "symbol": "spot",
            "condition": "price_above",
            "value": 95,
            "channels": ["in_app", "email"],
            "priority": "high",
        },
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert (rule["symbol"], rule["channels"]) == ("SPOT", ["email", "in_app"])
    assert rule["description"] == "When SPOT trades above 95.00: email and in-app, high priority."
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert message is not None
    assert message["data"] == "rules"

    async def refused(payload: dict[str, object]) -> str:
        response = await signed_in.post("/api/alert-rules", json=payload)
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        return detail if isinstance(detail, str) else detail[0]["msg"]

    base = {"name": "x", "scope": "ticker", "symbol": "SPOT", "channels": ["in_app"]}
    assert "positive number" in await refused({**base, "condition": "price_above"})
    assert "moving average" in await refused({**base, "condition": "ma_cross_below"})
    assert "No stock NOPE" in await refused(
        {**base, "symbol": "nope", "condition": "change_above", "value": 5}
    )
    assert "screen alert" in await refused({**base, "condition": "new_match"})
    assert "at least 1" in await refused(
        {**base, "condition": "change_above", "value": 5, "channels": []}
    )

    ma = await signed_in.post(
        "/api/alert-rules",
        json={
            "name": "Leaders lose the 50-day",
            "scope": "watchlist",
            "watchlist_id": watchlist.id,
            "condition": "ma_cross_below",
            "ma": "sma50",
            "channels": ["in_app"],
        },
    )
    assert ma.json()["description"] == (
        "When any stock in Leaders crosses below its 50-day SMA: in-app, normal priority."
    )
    from_screen = await signed_in.post(
        "/api/alert-rules",
        json={
            "name": "New VCPs",
            "scope": "screen",
            "screen_id": screen.id,
            "condition": "new_match",
            "channels": ["email"],
        },
    )
    assert from_screen.json()["description"] == (
        "When a stock newly matching “VCPs near pivot” after the close: email, normal priority."
    )

    patched = await signed_in.patch(
        f"/api/alert-rules/{rule['id']}", json={"value": 97.5, "enabled": False}
    )
    assert (patched.json()["value"], patched.json()["enabled"]) == (97.5, False)
    bad = await signed_in.patch(f"/api/alert-rules/{rule['id']}", json={"value": -1})
    assert bad.status_code == 422
    assert len((await signed_in.get("/api/alert-rules")).json()) == 3
    assert (await signed_in.delete(f"/api/alert-rules/{rule['id']}")).status_code == 204
    assert (await signed_in.delete(f"/api/alert-rules/{rule['id']}")).status_code == 404
    await pubsub.aclose()  # type: ignore[no-untyped-call]

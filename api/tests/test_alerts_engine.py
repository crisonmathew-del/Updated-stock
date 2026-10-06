"""The alerts engine: who gets an alert, once, and what each channel does with it."""

import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.engine import (
    LIVE_CHANNEL,
    AlertDraft,
    EmailRoute,
    email_delivery,
    passes_grade,
    raise_alerts,
)
from app.core.calendar import MARKET_TZ
from app.core.redis import get_redis
from app.data.universe import plan_universe, sync_tickers
from app.intraday.store import ticker_ids
from app.models import Alert, AlertRule, User, Watchlist, WatchlistItem
from app.settings.schema import AppSettings
from tests.test_universe import listed

DAY = date(2026, 10, 2)
NOON = datetime(2026, 10, 2, 12, 0, tzinfo=MARKET_TZ)
ON = EmailRoute(True)
SETTINGS = AppSettings()


def draft(**changes: object) -> AlertDraft:
    base: dict[str, object] = {
        "kind": "breakout_provisional",
        "priority": "high",
        "title": "SPOT breaking out (provisional)",
        "body": "92.80 is above the 92.46 pivot.",
        "session_date": DAY,
        "dedupe_key": "breakout:5",
        "symbol": "SPOT",
        "grade": "A",
    }
    base.update(changes)
    return AlertDraft(**base)  # type: ignore[arg-type]


def test_email_goes_now_in_the_digest_or_nowhere() -> None:
    assert email_delivery(draft(), SETTINGS, ON, NOON) == "queued"
    assert email_delivery(draft(priority="normal"), SETTINGS, ON, NOON) == "digest"
    everything_now = AppSettings(alert_email_immediate_priority="normal")
    assert email_delivery(draft(priority="normal"), everything_now, ON, NOON) == "queued"
    assert email_delivery(draft(channels=("in_app",)), SETTINGS, ON, NOON) == "off for this rule"
    off = AppSettings(alerts_email_enabled=False)
    assert email_delivery(draft(), off, ON, NOON) == "off in settings"
    missing = EmailRoute(False, "set SMTP_HOST")
    assert email_delivery(draft(), SETTINGS, missing, NOON) == "not configured: set SMTP_HOST"


def test_quiet_hours_hold_immediate_emails_including_across_midnight() -> None:
    lunch = AppSettings(quiet_hours_start="11:30", quiet_hours_end="13:00")
    assert email_delivery(draft(), lunch, ON, NOON) == "held: quiet hours"
    assert email_delivery(draft(), lunch, ON, NOON.replace(hour=13, minute=1)) == "queued"
    night = AppSettings(quiet_hours_start="22:00", quiet_hours_end="07:00")
    late = datetime(2026, 10, 2, 23, 30, tzinfo=MARKET_TZ)
    early = datetime(2026, 10, 3, 6, 59, tzinfo=MARKET_TZ)
    assert email_delivery(draft(), night, ON, late) == "held: quiet hours"
    assert email_delivery(draft(), night, ON, early) == "held: quiet hours"
    assert email_delivery(draft(), night, ON, NOON) == "queued"
    # Quiet hours are US/Eastern: 16:00 UTC is noon in New York (EDT).
    assert email_delivery(draft(), lunch, ON, datetime(2026, 10, 2, 16, 0, tzinfo=UTC)) == (
        "held: quiet hours"
    )


def test_setup_alerts_need_the_grade_unless_you_follow_the_stock() -> None:
    assert passes_grade(draft(grade="A"), SETTINGS, set())
    assert not passes_grade(draft(grade="B"), SETTINGS, set())
    assert not passes_grade(draft(grade=None), SETTINGS, set())
    assert passes_grade(draft(grade="B", ticker_id=7), SETTINGS, {7})
    assert passes_grade(draft(grade="B"), AppSettings(alert_min_grade="B"), set())
    # Not a setup alert: no grade needed.
    assert passes_grade(draft(kind="rule", grade=None), SETTINGS, set())
    assert passes_grade(draft(kind="regime_change", grade=None), SETTINGS, set())


@pytest.mark.integration
async def test_alerts_are_recorded_once_and_published(db: AsyncSession, user: User) -> None:
    await sync_tickers(db, plan_universe(listed("SPOT", "AAPL")), DAY)
    ids = await ticker_ids(db, ["SPOT", "AAPL"])
    watchlist = Watchlist(user_id=user.id, name="Leaders")
    db.add(watchlist)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=watchlist.id, ticker_id=ids["AAPL"]))
    await db.commit()

    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(LIVE_CHANNEL)
    await pubsub.get_message(timeout=1)  # the subscribe confirmation

    drafts = [
        draft(ticker_id=ids["SPOT"]),
        draft(ticker_id=ids["SPOT"]),  # the same alert again: dropped
        draft(  # a B setup on a stock nobody watches: dropped
            ticker_id=ids["SPOT"], kind="near_pivot", grade="B", dedupe_key="near:SPOT"
        ),
        draft(  # a B setup on a watched stock: kept, normal priority goes to the digest
            ticker_id=ids["AAPL"],
            symbol="AAPL",
            kind="near_pivot",
            priority="normal",
            grade="B",
            dedupe_key="near:AAPL",
        ),
    ]
    created = await raise_alerts(db, redis, drafts, SETTINGS, NOON, ON)
    assert [(a.symbol, a.kind, a.delivery) for a in created] == [
        ("SPOT", "breakout_provisional", {"in_app": "sent", "email": "queued"}),
        ("AAPL", "near_pivot", {"in_app": "sent", "email": "digest"}),
    ]
    stored = (await db.scalars(select(Alert).order_by(Alert.id))).all()
    assert [a.user_id for a in stored] == [user.id, user.id]

    messages = []
    for _ in range(2):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
        assert message is not None
        messages.append(json.loads(message["data"]))
    assert [(m["type"], m["user_id"], m["data"]["symbol"]) for m in messages] == [
        ("alert", user.id, "SPOT"),
        ("alert", user.id, "AAPL"),
    ]
    assert messages[0]["data"]["title"] == "SPOT breaking out (provisional)"
    await pubsub.aclose()  # type: ignore[no-untyped-call]

    # Raised again later (another process, same cooldown): nothing new.
    assert await raise_alerts(db, redis, drafts[:1], SETTINGS, NOON, ON) == []
    # Without a cooldown every raise alerts.
    no_cooldown = AppSettings(alert_cooldown_minutes=0)
    assert len(await raise_alerts(db, redis, drafts[:1], no_cooldown, NOON, ON)) == 1


@pytest.mark.integration
async def test_rule_alerts_follow_their_channels(db: AsyncSession, user: User) -> None:
    rule = AlertRule(
        user_id=user.id,
        name="Over 95",
        scope="ticker",
        condition="price_above",
        value=95,
        channels=["email"],
        priority="high",
    )
    db.add(rule)
    await db.commit()
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(LIVE_CHANNEL)
    await pubsub.get_message(timeout=1)
    created = await raise_alerts(
        db,
        redis,
        [
            draft(
                kind="rule",
                user_id=user.id,
                rule_id=rule.id,
                channels=("email",),
                dedupe_key=f"rule:{rule.id}",
            )
        ],
        SETTINGS,
        NOON,
        ON,
    )
    assert [a.delivery for a in created] == [{"in_app": "off for this rule", "email": "queued"}]
    assert created[0].read_at is not None  # never shows as unread
    # Nothing was pushed to open pages.
    assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3) is None
    await pubsub.aclose()  # type: ignore[no-untyped-call]
    await db.refresh(rule)
    assert rule.last_fired_at == NOON


@pytest.mark.integration
async def test_a_draft_for_one_user_reaches_only_that_user(db: AsyncSession, user: User) -> None:
    other = User(email="partner@example.com", password_hash="x")
    db.add(other)
    await db.commit()
    redis = get_redis()
    everyone = await raise_alerts(db, redis, [draft(dedupe_key="all")], SETTINGS, NOON, ON)
    assert sorted(a.user_id for a in everyone) == sorted([user.id, other.id])
    one = await raise_alerts(
        db, redis, [draft(dedupe_key="mine", user_id=other.id)], SETTINGS, NOON, ON
    )
    assert [a.user_id for a in one] == [other.id]

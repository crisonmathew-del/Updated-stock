"""The alerts engine (spec §7.2-7.3): turns drafts from the intraday watcher, the EOD scan and
the user's rules into alerts, once, and routes each to its channels.

For every draft and recipient:
1. **Who.** A draft for one user (a rule, a holding) goes to that user; setup and market
   drafts go to every user. Setup alerts on stocks the user neither holds nor watches need
   the setup's grade to reach `alert_min_grade`.
2. **Once.** The same `dedupe_key` alerts a user at most once per `alert_cooldown_minutes`
   (default 390: once a session). A Redis key with that expiry holds the claim, so the
   streamer, the worker and the API never double-send.
3. **Channels.** In-app always records the alert (the alerts centre is the log) and pushes it
   to open pages through the `live:events` channel. Email is immediate for priorities at or
   above `alert_email_immediate_priority` outside quiet hours; otherwise the alert waits for the
   daily digest (held, or digest). A rule can switch either channel off.

`delivery` records what each channel did, in words the alerts centre shows.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar import MARKET_TZ
from app.core.logging import get_logger
from app.intraday.session import in_window
from app.models import Alert, AlertRule, Holding, User, Watchlist, WatchlistItem
from app.scanner.evaluate import SIGNAL_LABELS
from app.settings.schema import AppSettings

log = get_logger(__name__)

LIVE_CHANNEL = "live:events"
PRIORITY_RANK = {"normal": 1, "high": 2}
GRADE_RANK = {"A+": 4, "A": 3, "B": 2, "C": 1}
CHANNELS = ("in_app", "email")
# Alerts about a setup: filtered by grade unless the user holds or watches the stock.
SETUP_KINDS = frozenset(
    {
        "new_top_setup",
        "near_pivot",
        "breakout",
        "breakout_provisional",
        "breakout_confirmed",
        "breakout_rejected",
        "breakout_extended",
        "extended",
        "failed",
        "invalidated",
        "setup_stop",
        "pocket_pivot",
        "earnings_gap",
        "rs_new_high_ahead",
        "pullback",
        "undercut_rally",
        "premarket_gap",
        "sweep",
    }
)


# How the alerts centre names each kind (signal kinds use app.scanner.evaluate.SIGNAL_LABELS).
KIND_LABELS = {
    "breakout_provisional": "Breakout (provisional)",
    "breakout_extended": "Past the buy zone",
    "setup_stop": "Stop hit",
    "holding_stop": "Holding stop",
    "holding_below_50": "Below the 50-day",
    "holding_below_21": "Below the 21-day",
    "holding_breakeven": "Raise stop to breakeven",
    "holding_profit_zone": "Profit-taking zone",
    "holding_earnings": "Earnings ahead",
    "rule": "Your rule",
    "screen_match": "Screen match",
    "premarket_gap": "Pre-market gap",
    "sweep": "Volume surge",
    "test": "Test",
}


@dataclass(frozen=True)
class AlertDraft:
    kind: str
    priority: str  # high | normal
    title: str
    body: str
    session_date: date
    dedupe_key: str
    symbol: str | None = None
    ticker_id: int | None = None
    user_id: int | None = None  # None: every user
    grade: str | None = None  # the setup's grade, for the grade filter
    payload: dict[str, Any] = field(default_factory=dict)
    signal_id: int | None = None
    rule_id: int | None = None
    holding_id: int | None = None
    channels: tuple[str, ...] | None = None  # None: in-app + email (as settings allow)


@dataclass(frozen=True)
class EmailRoute:
    """How email will be handled for alerts raised now (one decision per raise call)."""

    configured: bool
    reason: str = ""  # when not configured: what to set


def quiet_now(settings: AppSettings, now: datetime) -> bool:
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    return bool(start and end) and in_window(now.astimezone(MARKET_TZ), start, end)


def email_delivery(
    draft: AlertDraft, settings: AppSettings, route: EmailRoute, now: datetime
) -> str:
    """What the email channel does with this alert, in the words the alerts centre shows."""
    if draft.channels is not None and "email" not in draft.channels:
        return "off for this rule"
    if not settings.alerts_email_enabled:
        return "off in settings"
    if not route.configured:
        return f"not configured: {route.reason}"
    immediate = PRIORITY_RANK.get(draft.priority, 1) >= PRIORITY_RANK.get(
        settings.alert_email_immediate_priority, 2
    )
    if not immediate:
        return "digest"
    if quiet_now(settings, now):
        return "held: quiet hours"
    return "queued"


def passes_grade(draft: AlertDraft, settings: AppSettings, followed: set[int]) -> bool:
    if draft.kind not in SETUP_KINDS or draft.ticker_id in followed:
        return True
    return GRADE_RANK.get(draft.grade or "", 0) >= GRADE_RANK[settings.alert_min_grade]


def dedupe_redis_key(user_id: int, key: str) -> str:
    return f"alert:dedupe:{user_id}:{key}"


async def claim(redis: Redis, user_id: int, key: str, cooldown_minutes: int) -> bool:
    """True when this alert hasn't fired for the user within the cooldown (and claims it)."""
    if cooldown_minutes <= 0:
        return True
    claimed = await redis.set(
        dedupe_redis_key(user_id, key), "1", nx=True, ex=cooldown_minutes * 60
    )
    return bool(claimed)


async def followed_tickers(session: AsyncSession, user_ids: Sequence[int]) -> dict[int, set[int]]:
    """Each user's watched and held stocks."""
    out: dict[int, set[int]] = {uid: set() for uid in user_ids}
    watched = await session.execute(
        select(Watchlist.user_id, WatchlistItem.ticker_id)
        .join(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
        .where(Watchlist.user_id.in_(user_ids))
    )
    held = await session.execute(
        select(Holding.user_id, Holding.ticker_id).where(
            Holding.user_id.in_(user_ids), Holding.closed_on.is_(None)
        )
    )
    for uid, tid in [*watched.all(), *held.all()]:
        out[int(uid)].add(int(tid))
    return out


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind) or SIGNAL_LABELS.get(kind) or kind.replace("_", " ").capitalize()


def alert_json(alert: Alert) -> dict[str, Any]:
    """The alert as the API and the live channel send it."""
    return {
        "id": alert.id,
        "created_at": alert.created_at.isoformat(),
        "session_date": alert.session_date.isoformat(),
        "kind": alert.kind,
        "kind_label": kind_label(alert.kind),
        "priority": alert.priority,
        "symbol": alert.symbol,
        "title": alert.title,
        "body": alert.body,
        "payload": alert.payload,
        "delivery": alert.delivery,
        "read": alert.read_at is not None,
    }


async def publish(redis: Redis, message: dict[str, Any]) -> None:
    await redis.publish(LIVE_CHANNEL, json.dumps(message, default=str))


async def raise_alerts(
    session: AsyncSession,
    redis: Redis,
    drafts: Sequence[AlertDraft],
    settings: AppSettings,
    now: datetime,
    route: EmailRoute,
) -> list[Alert]:
    """Record and route `drafts`; returns the alerts created (those whose email is "queued"
    are for the caller to send, see app.alerts.delivery)."""
    if not drafts:
        return []
    users = [int(u) for u in await session.scalars(select(User.id))]
    followed = await followed_tickers(session, users)
    created: list[Alert] = []
    for draft in drafts:
        for uid in [draft.user_id] if draft.user_id is not None else users:
            if uid not in followed:
                continue
            if not passes_grade(draft, settings, followed[uid]):
                continue
            if not await claim(redis, uid, draft.dedupe_key, settings.alert_cooldown_minutes):
                continue
            in_app = draft.channels is None or "in_app" in draft.channels
            alert = Alert(
                user_id=uid,
                created_at=now,
                session_date=draft.session_date,
                kind=draft.kind,
                priority=draft.priority,
                ticker_id=draft.ticker_id,
                symbol=draft.symbol,
                title=draft.title[:200],
                body=draft.body,
                payload=draft.payload,
                signal_id=draft.signal_id,
                rule_id=draft.rule_id,
                holding_id=draft.holding_id,
                dedupe_key=draft.dedupe_key[:160],
                delivery={
                    "in_app": "sent" if in_app else "off for this rule",
                    "email": email_delivery(draft, settings, route, now),
                },
                read_at=None if in_app else now,
            )
            session.add(alert)
            created.append(alert)
    if not created:
        return []
    rule_ids = {a.rule_id for a in created if a.rule_id is not None}
    if rule_ids:
        await session.execute(
            update(AlertRule).where(AlertRule.id.in_(rule_ids)).values(last_fired_at=now)
        )
    await session.commit()
    for alert in created:
        if alert.delivery["in_app"] == "sent":
            await publish(
                redis, {"type": "alert", "user_id": alert.user_id, "data": alert_json(alert)}
            )
    log.info(
        "alerts.raised",
        count=len(created),
        kinds=sorted({a.kind for a in created}),
        emails=sum(1 for a in created if a.delivery["email"] == "queued"),
    )
    return created

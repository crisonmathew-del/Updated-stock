"""Alerts (spec §7.2-7.3, §8.7): the alerts centre's history and unread count, alert rules
(a saved screen promoted to an alert is a rule with scope "screen"), test sends and the
delivery status.

- GET    /alerts                ?kind=&priority=&symbol=&unread=&before=&limit=  newest first
- GET    /alerts/unread         {count}
- POST   /alerts/read           {ids?: [...]}: mark those (or all) read
- POST   /alerts/test           {channel: in_app | email}: send a test alert now
- GET    /alerts/status         the email channel and the streamer, in words
- GET    /alert-rules
- POST   /alert-rules
- PATCH  /alert-rules/{id}
- DELETE /alert-rules/{id}
A change to rules tells the streamer to reload its watch plan (`watch:refresh`).
"""

import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.alerts.email import email_route
from app.alerts.engine import AlertDraft, alert_json, kind_label, quiet_now, raise_alerts
from app.alerts.jobs import email_alerts
from app.api.deps import AuthUser, DbSession, RedisClient, current_user
from app.core.calendar import MARKET_TZ
from app.core.config import get_settings
from app.core.heartbeat import heartbeat_key
from app.intraday.live import REFRESH_CHANNEL
from app.intraday.service import STATUS_KEY
from app.models import Alert, AlertRule, SavedScreen, Ticker, Watchlist
from app.scanner.intraday_scan import MA_LABELS
from app.settings import store

router = APIRouter(tags=["alerts"], dependencies=[Depends(current_user)])

Scope = Literal["ticker", "watchlist", "holdings", "screen"]
Condition = Literal[
    "price_above",
    "price_below",
    "ma_cross_above",
    "ma_cross_below",
    "change_above",
    "change_below",
    "volume_ratio_above",
    "new_match",
]
MovingAverage = Literal["ema10", "ema21", "sma50", "sma150", "sma200"]
Channel = Literal["in_app", "email"]
Priority = Literal["high", "normal"]
PAGE = 50


class AlertOut(BaseModel):
    id: int
    created_at: datetime
    session_date: date
    kind: str
    kind_label: str
    priority: str
    symbol: str | None
    title: str
    body: str
    payload: dict[str, Any]
    delivery: dict[str, Any]
    read: bool


class KindCount(BaseModel):
    kind: str
    label: str
    count: int


class AlertsPage(BaseModel):
    items: list[AlertOut]
    unread: int
    kinds: list[KindCount]
    next_before: int | None


class ReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=1000)


class TestIn(BaseModel):
    channel: Channel


class Unread(BaseModel):
    count: int


class ChannelStatus(BaseModel):
    configured: bool
    provider: str | None
    detail: str


class StreamerStatus(BaseModel):
    alive: bool
    state: str
    provider: str | None
    detail: str | None
    since: datetime | None


class AlertsStatus(BaseModel):
    email: ChannelStatus
    streamer: StreamerStatus
    quiet_hours_now: bool


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    scope: Scope
    symbol: str | None = Field(default=None, max_length=16)
    watchlist_id: int | None = None
    screen_id: int | None = None
    condition: Condition
    value: float | None = None
    ma: MovingAverage | None = None
    channels: list[Channel] = Field(min_length=1, max_length=2)
    priority: Priority = "normal"


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    value: float | None = None
    ma: MovingAverage | None = None
    channels: list[Channel] | None = Field(default=None, min_length=1, max_length=2)
    priority: Priority | None = None


class RuleOut(BaseModel):
    id: int
    name: str
    enabled: bool
    scope: str
    symbol: str | None
    watchlist_id: int | None
    watchlist_name: str | None
    screen_id: int | None
    screen_name: str | None
    condition: str
    value: float | None
    ma: str | None
    channels: list[str]
    priority: str
    description: str
    last_fired_at: datetime | None


def _out(alert: Alert) -> AlertOut:
    return AlertOut(**alert_json(alert))


def describe_rule(
    rule: AlertRule, symbol: str | None, watchlist: str | None, screen: str | None
) -> str:
    target = {
        "ticker": symbol or "the stock",
        "watchlist": f"any stock in {watchlist or 'the watchlist'}",
        "holdings": "any of your holdings",
        "screen": f"a stock newly matching “{screen or 'the screen'}”",
    }[rule.scope]
    v = rule.value
    ma = MA_LABELS.get(rule.ma or "", rule.ma or "moving average")
    what = {
        "price_above": f"trades above {v:,.2f}" if v is not None else "",
        "price_below": f"trades below {v:,.2f}" if v is not None else "",
        "ma_cross_above": f"crosses above its {ma}",
        "ma_cross_below": f"crosses below its {ma}",
        "change_above": f"is up {v:g}% or more on the day" if v is not None else "",
        "change_below": f"is down {abs(v):g}% or more on the day" if v is not None else "",
        "volume_ratio_above": f"projects {v:g}× its average volume" if v is not None else "",
        "new_match": "after the close",
    }[rule.condition]
    channels = " and ".join("in-app" if c == "in_app" else "email" for c in rule.channels)
    return f"When {target} {what}: {channels}, {rule.priority} priority."


async def _rule_out(db: DbSession, rule: AlertRule) -> RuleOut:
    ticker = await db.get(Ticker, rule.ticker_id) if rule.ticker_id else None
    symbol = None if ticker is None else ticker.symbol
    watchlist = await db.get(Watchlist, rule.watchlist_id) if rule.watchlist_id else None
    screen = await db.get(SavedScreen, rule.screen_id) if rule.screen_id else None
    return RuleOut(
        id=rule.id,
        name=rule.name,
        enabled=rule.enabled,
        scope=rule.scope,
        symbol=symbol,
        watchlist_id=rule.watchlist_id,
        watchlist_name=None if watchlist is None else watchlist.name,
        screen_id=rule.screen_id,
        screen_name=None if screen is None else screen.name,
        condition=rule.condition,
        value=rule.value,
        ma=rule.ma,
        channels=list(rule.channels),
        priority=rule.priority,
        description=describe_rule(
            rule,
            symbol,
            None if watchlist is None else watchlist.name,
            None if screen is None else screen.name,
        ),
        last_fired_at=rule.last_fired_at,
    )


def _check_condition(condition: str, value: float | None, ma: str | None) -> None:
    def bad(message: str) -> HTTPException:
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)

    if condition in ("price_above", "price_below") and (value is None or value <= 0):
        raise bad("Give the price to watch (a positive number).")
    if condition in ("change_above", "change_below") and value is None:
        raise bad("Give the % change on the day to watch (e.g. 5 or -4).")
    if condition == "volume_ratio_above" and (value is None or value <= 0):
        raise bad("Give the multiple of average volume (e.g. 2 for twice the average).")
    if condition.startswith("ma_cross") and ma is None:
        raise bad("Choose the moving average to watch.")


async def _resolve_target(db: DbSession, user: AuthUser, payload: RuleIn) -> dict[str, Any]:
    def bad(message: str) -> HTTPException:
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)

    if (payload.scope == "screen") != (payload.condition == "new_match"):
        raise bad("A screen alert fires on new matches; other alerts watch a price or volume.")
    target: dict[str, Any] = {"ticker_id": None, "watchlist_id": None, "screen_id": None}
    if payload.scope == "ticker":
        if not payload.symbol:
            raise bad("Choose the stock to watch.")
        tid = await db.scalar(
            select(Ticker.id).where(Ticker.symbol == payload.symbol.strip().upper())
        )
        if tid is None:
            raise bad(f"No stock {payload.symbol.upper()}.")
        target["ticker_id"] = tid
    elif payload.scope == "watchlist":
        found = await db.get(Watchlist, payload.watchlist_id) if payload.watchlist_id else None
        if found is None or found.user_id != user.id:
            raise bad("Choose one of your watchlists.")
        target["watchlist_id"] = found.id
    elif payload.scope == "screen":
        screen = await db.get(SavedScreen, payload.screen_id) if payload.screen_id else None
        if screen is None or screen.user_id != user.id:
            raise bad("Choose one of your saved screens.")
        target["screen_id"] = screen.id
    return target


async def _own_rule(db: DbSession, user: AuthUser, rule_id: int) -> AlertRule:
    rule = await db.get(AlertRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No alert rule {rule_id}.")
    return rule


async def _refresh_streamer(redis: RedisClient) -> None:
    await redis.publish(REFRESH_CHANNEL, "rules")


@router.get("/alerts")
async def list_alerts(
    db: DbSession,
    user: AuthUser,
    kind: str | None = Query(default=None, max_length=200),
    priority: Priority | None = None,
    symbol: str | None = Query(default=None, max_length=16),
    unread: bool = False,
    before: int | None = None,
    limit: int = Query(default=PAGE, ge=1, le=200),
) -> AlertsPage:
    """Newest first; `kind` takes a comma-separated list; `before` is the last id seen."""
    query = select(Alert).where(Alert.user_id == user.id)
    if kind:
        query = query.where(Alert.kind.in_([k.strip() for k in kind.split(",") if k.strip()]))
    if priority:
        query = query.where(Alert.priority == priority)
    if symbol:
        query = query.where(Alert.symbol == symbol.strip().upper())
    if unread:
        query = query.where(Alert.read_at.is_(None))
    if before is not None:
        query = query.where(Alert.id < before)
    rows = list(await db.scalars(query.order_by(Alert.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    unread_count = await db.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.user_id == user.id, Alert.read_at.is_(None))
    )
    kinds = await db.execute(
        select(Alert.kind, func.count())
        .where(Alert.user_id == user.id)
        .group_by(Alert.kind)
        .order_by(func.count().desc())
    )
    return AlertsPage(
        items=[_out(a) for a in rows],
        unread=int(unread_count or 0),
        kinds=[KindCount(kind=k, label=kind_label(k), count=int(n)) for k, n in kinds.all()],
        next_before=rows[-1].id if more and rows else None,
    )


@router.get("/alerts/unread")
async def unread_count(db: DbSession, user: AuthUser) -> Unread:
    count = await db.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.user_id == user.id, Alert.read_at.is_(None))
    )
    return Unread(count=int(count or 0))


@router.post("/alerts/read")
async def mark_read(db: DbSession, user: AuthUser, payload: ReadIn) -> Unread:
    stmt = (
        update(Alert)
        .where(Alert.user_id == user.id, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    if payload.ids is not None:
        stmt = stmt.where(Alert.id.in_(payload.ids))
    await db.execute(stmt)
    await db.commit()
    return await unread_count(db, user)


@router.post("/alerts/test")
async def test_alert(
    db: DbSession, redis: RedisClient, user: AuthUser, payload: TestIn
) -> AlertOut:
    """Send a test alert on one channel now (ignores quiet hours and the email switch)."""
    config = get_settings()
    settings = (await store.load(db)).model_copy(
        update={
            "alerts_email_enabled": True,
            "quiet_hours_start": "",
            "quiet_hours_end": "",
            "alert_cooldown_minutes": 0,
        }
    )
    now = datetime.now(UTC)
    where = "in the app" if payload.channel == "in_app" else "by email"
    draft = AlertDraft(
        kind="test",
        priority="high",
        title=f"Test alert ({'in-app' if payload.channel == 'in_app' else 'email'})",
        body=f"Alerts reach you {where}. Sent {now.astimezone(MARKET_TZ):%H:%M:%S} ET.",
        session_date=now.astimezone(MARKET_TZ).date(),
        dedupe_key=f"test:{now.timestamp()}",
        user_id=user.id,
        channels=(payload.channel,),
    )
    [alert] = await raise_alerts(db, redis, [draft], settings, now, email_route(config))
    if alert.delivery.get("email") == "queued":
        await email_alerts(db, [alert.id])
        await db.refresh(alert)
    return _out(alert)


@router.get("/alerts/status")
async def alerts_status(db: DbSession, redis: RedisClient) -> AlertsStatus:
    config = get_settings()
    route = email_route(config)
    if config.email_provider == "resend" and config.resend_api_key is not None:
        provider: str | None = "resend"
    elif config.email_provider == "smtp" and config.smtp_host:
        provider = "smtp"
    elif config.mail_catcher_host:
        provider = "mailpit"
    else:
        provider = None
    detail = {
        "resend": f"Resend, from {config.email_from}",
        "smtp": f"SMTP via {config.smtp_host}:{config.smtp_port}",
        "mailpit": "Development mail catcher (Mailpit, http://localhost:8025): nothing leaves "
        "this machine",
    }.get(provider or "", route.reason)
    raw = await redis.get(STATUS_KEY)
    state: dict[str, Any] = json.loads(raw) if raw else {}
    alive = bool(await redis.exists(heartbeat_key("streamer")))
    settings = await store.load(db)
    return AlertsStatus(
        email=ChannelStatus(
            configured=route.configured,
            provider=provider,
            detail=detail if route.configured else route.reason,
        ),
        streamer=StreamerStatus(
            alive=alive,
            state=state.get("state", "unknown") if alive else "down",
            provider=state.get("provider", config.stream_provider),
            detail=state.get("detail"),
            since=state.get("at"),
        ),
        quiet_hours_now=quiet_now(settings, datetime.now(UTC)),
    )


@router.get("/alert-rules")
async def list_rules(db: DbSession, user: AuthUser) -> list[RuleOut]:
    rules = await db.scalars(
        select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.id)
    )
    return [await _rule_out(db, r) for r in rules]


@router.post("/alert-rules", status_code=status.HTTP_201_CREATED)
async def create_rule(
    db: DbSession, redis: RedisClient, user: AuthUser, payload: RuleIn
) -> RuleOut:
    _check_condition(payload.condition, payload.value, payload.ma)
    target = await _resolve_target(db, user, payload)
    rule = AlertRule(
        user_id=user.id,
        name=payload.name.strip(),
        enabled=payload.enabled,
        scope=payload.scope,
        condition=payload.condition,
        value=payload.value,
        ma=payload.ma if payload.condition.startswith("ma_cross") else None,
        channels=sorted(set(payload.channels)),
        priority=payload.priority,
        **target,
    )
    db.add(rule)
    await db.commit()
    await _refresh_streamer(redis)
    return await _rule_out(db, rule)


@router.patch("/alert-rules/{rule_id}")
async def update_rule(
    db: DbSession, redis: RedisClient, user: AuthUser, rule_id: int, payload: RulePatch
) -> RuleOut:
    rule = await _own_rule(db, user, rule_id)
    changes = payload.model_dump(exclude_unset=True)
    value = changes.get("value", rule.value)
    ma = changes.get("ma", rule.ma)
    _check_condition(rule.condition, value, ma)
    for key, new in changes.items():
        setattr(rule, key, sorted(set(new)) if key == "channels" else new)
    if "name" in changes:
        rule.name = rule.name.strip()
    await db.commit()
    await _refresh_streamer(redis)
    return await _rule_out(db, rule)


@router.delete("/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(db: DbSession, redis: RedisClient, user: AuthUser, rule_id: int) -> Response:
    rule = await _own_rule(db, user, rule_id)
    await db.delete(rule)
    await db.commit()
    await _refresh_streamer(redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

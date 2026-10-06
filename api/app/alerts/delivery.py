"""Sending: queued alert emails (one per alert, with a mini chart) and the daily and weekly
digests. Every outcome is written back to the alert's `delivery` so the alerts centre shows
what happened ("sent", "failed: SMTP … refused", "digest: sent").

The daily digest goes out on trading days at `daily_digest_time` (US/Eastern); the weekly
review on Sundays at 18:00. Each lists the alerts not emailed one by one yet (lower priority,
held for quiet hours or failed), then the best setups and the market. A Redis key per period
makes sending idempotent: the scheduler's tick can call `send_due_digests` as often as it likes.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.email import EmailError, EmailSender
from app.alerts.engine import alert_json
from app.alerts.render import (
    ChartBar,
    Digest,
    DigestSetup,
    alert_message,
    digest_message,
    mini_chart,
)
from app.core.calendar import MARKET_TZ, is_session
from app.core.logging import get_logger
from app.intraday.session import at
from app.market.regime import LABELS as REGIME_LABELS
from app.market.regime import MARKET, RegimeState
from app.models import (
    Alert,
    DailyBar,
    Holding,
    MarketRegimeDaily,
    Setup,
    Signal,
    Ticker,
    User,
)
from app.scanner.evaluate import SIGNAL_LABELS
from app.settings.schema import AppSettings

log = get_logger(__name__)

CHART_SESSIONS = 60
DIGEST_SETUPS = 10
WEEKLY_DIGEST_TIME = "18:00"
PENDING = ("digest", "held: quiet hours")


async def recipient(session: AsyncSession, user_id: int, email_to: str | None) -> str | None:
    if email_to:
        return email_to
    user = await session.get(User, user_id)
    return None if user is None else user.email


async def chart_bars(
    session: AsyncSession, ticker_id: int, through: date, include_through: bool
) -> list[ChartBar]:
    cond = DailyBar.date <= through if include_through else DailyBar.date < through
    rows = await session.execute(
        select(DailyBar.date, DailyBar.high, DailyBar.low, DailyBar.close)
        .where(DailyBar.ticker_id == ticker_id, cond)
        .order_by(DailyBar.date.desc())
        .limit(CHART_SESSIONS)
    )
    return [ChartBar(d, float(h), float(lo), float(c)) for d, h, lo, c in reversed(rows.all())]


async def chart_for(session: AsyncSession, alert: Alert) -> bytes | None:
    if alert.ticker_id is None:
        return None
    payload = alert.payload or {}
    intraday = bool(payload.get("intraday"))
    bars = await chart_bars(session, alert.ticker_id, alert.session_date, not intraday)
    return mini_chart(
        bars,
        last=payload.get("price") if intraday else None,
        pivot=payload.get("pivot"),
        buy_zone_top=payload.get("buy_zone_top"),
        stop=payload.get("stop"),
    )


async def send_alert_emails(
    session: AsyncSession,
    sender: EmailSender | None,
    alert_ids: Sequence[int],
    *,
    public_url: str,
    email_to: str | None,
) -> Counter[str]:
    """Email every alert in `alert_ids` still marked "queued"."""
    counts: Counter[str] = Counter()
    if not alert_ids:
        return counts
    alerts = list(
        await session.scalars(select(Alert).where(Alert.id.in_(alert_ids)).order_by(Alert.id))
    )
    for alert in alerts:
        if alert.delivery.get("email") != "queued":
            continue
        to = await recipient(session, alert.user_id, email_to)
        if sender is None or to is None:
            outcome = (
                "failed: no email provider configured" if sender is None else "failed: no address"
            )
        else:
            try:
                chart = await chart_for(session, alert)
                await sender.send(alert_message(alert_json(alert), to, public_url, chart))
                outcome = "sent"
            except EmailError as exc:
                outcome = f"failed: {exc}"
                log.warning("alerts.email_failed", alert_id=alert.id, error=str(exc))
        alert.delivery = {**alert.delivery, "email": outcome}
        counts[outcome.split(":")[0]] += 1
    await session.commit()
    if counts:
        log.info("alerts.emails", **dict(counts))
    return counts


# --- Digests ----------------------------------------------------------------------------------


def due_period(kind: str, settings: AppSettings, now: datetime) -> str | None:
    """The period a digest of `kind` is due for at `now`, or None."""
    local = now.astimezone(MARKET_TZ)
    today = local.date()
    if kind == "daily":
        if not settings.daily_digest_enabled or not is_session(today):
            return None
        return today.isoformat() if local >= at(today, settings.daily_digest_time) else None
    if kind == "weekly":
        if not settings.weekly_digest_enabled or today.weekday() != 6:
            return None
        return local.strftime("%G-W%V") if local >= at(today, WEEKLY_DIGEST_TIME) else None
    raise ValueError(f"unknown digest kind {kind!r}")


async def _regime_line(session: AsyncSession, day: date) -> str | None:
    row = await session.scalar(
        select(MarketRegimeDaily)
        .where(MarketRegimeDaily.index_symbol == MARKET, MarketRegimeDaily.date <= day)
        .order_by(MarketRegimeDaily.date.desc())
        .limit(1)
    )
    if row is None:
        return None
    label = REGIME_LABELS.get(RegimeState(row.state), row.state)
    return (
        f"Market: {label} ({row.distribution_days} distribution day"
        f"{'s' if row.distribution_days != 1 else ''}, as of {row.date:%b %-d})."
    )


async def _top_setups(session: AsyncSession) -> list[DigestSetup]:
    rows = await session.execute(
        select(Ticker.symbol, Setup)
        .join(Ticker, Ticker.id == Setup.ticker_id)
        .where(
            Setup.active,
            Setup.state.in_(("basing", "near_pivot")),
            Setup.grade.in_(("A+", "A")),
        )
        .order_by(Setup.score.desc())
        .limit(DIGEST_SETUPS)
    )
    return [
        DigestSetup(
            symbol,
            s.grade,
            s.score,
            s.state.replace("_", " "),
            s.pattern_type.replace("_", " ") if s.pattern_type else None,
            s.pivot,
            s.readiness_pct,
        )
        for symbol, s in rows.all()
    ]


async def _weekly_lines(session: AsyncSession, user_id: int, start: date, end: date) -> list[str]:
    lines: list[str] = []
    counts = await session.execute(
        select(Signal.type, func.count())
        .where(Signal.date >= start, Signal.date <= end, Signal.ticker_id.is_not(None))
        .group_by(Signal.type)
        .order_by(func.count().desc())
    )
    found = [(SIGNAL_LABELS.get(t, t), int(n)) for t, n in counts.all()]
    if found:
        lines.append("Signals this week: " + ", ".join(f"{label} {n}" for label, n in found) + ".")
    else:
        lines.append("No stock signals this week.")
    latest_close = (
        select(DailyBar.close)
        .where(DailyBar.ticker_id == Holding.ticker_id)
        .order_by(DailyBar.date.desc())
        .limit(1)
        .scalar_subquery()
    )
    held = await session.execute(
        select(Ticker.symbol, Holding, latest_close)
        .join(Ticker, Ticker.id == Holding.ticker_id)
        .where(Holding.user_id == user_id, Holding.closed_on.is_(None))
        .order_by(Ticker.symbol)
    )
    parts = []
    for symbol, h, close in held.all():
        risk = h.entry_price - h.initial_stop
        if close is None or risk <= 0:
            continue
        parts.append(f"{symbol} {(float(close) - h.entry_price) / risk:+.1f}R")
    if parts:
        lines.append("Your holdings: " + ", ".join(parts) + ".")
    return lines


async def build_digest(
    session: AsyncSession, user_id: int, kind: str, now: datetime
) -> tuple[Digest, list[Alert]]:
    local = now.astimezone(MARKET_TZ)
    today = local.date()
    pending = list(
        await session.scalars(
            select(Alert)
            .where(
                Alert.user_id == user_id,
                Alert.digested_at.is_(None),
                Alert.created_at <= now,
                Alert.delivery["email"].astext.in_(PENDING)
                | Alert.delivery["email"].astext.like("failed%"),
            )
            .order_by(Alert.priority, Alert.created_at)  # "high" sorts first
        )
    )
    if kind == "daily":
        emailed = await session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.user_id == user_id,
                Alert.session_date == today,
                Alert.delivery["email"].astext == "sent",
            )
        )
        digest = Digest(
            "daily",
            "Daily digest",
            f"{local:%A, %B %-d}",
            await _regime_line(session, today),
            [alert_json(a) for a in pending],
            int(emailed or 0),
            await _top_setups(session),
            [],
        )
    else:
        start = today - timedelta(days=6)
        emailed = await session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.user_id == user_id,
                Alert.session_date >= start,
                Alert.delivery["email"].astext == "sent",
            )
        )
        digest = Digest(
            "weekly",
            "Weekly review",
            f"Week of {start:%B %-d}",
            await _regime_line(session, today),
            [alert_json(a) for a in pending],
            int(emailed or 0),
            await _top_setups(session),
            await _weekly_lines(session, user_id, start, today),
        )
    return digest, pending


async def send_digest(
    session: AsyncSession,
    sender: EmailSender | None,
    kind: str,
    now: datetime,
    *,
    public_url: str,
    email_to: str | None,
) -> dict[str, Any]:
    """Build and email the digest for every user; marks the included alerts digested."""
    if sender is None:
        return {"skipped": "no email provider configured"}
    sent = 0
    for user_id in [int(u) for u in await session.scalars(select(User.id))]:
        to = await recipient(session, user_id, email_to)
        if to is None:
            continue
        digest, pending = await build_digest(session, user_id, kind, now)
        await sender.send(digest_message(digest, to, public_url))
        for alert in pending:
            alert.digested_at = now
            alert.delivery = {**alert.delivery, "email": f"{kind} digest: sent"}
        sent += 1
    await session.commit()
    log.info("alerts.digest_sent", kind=kind, users=sent)
    return {"kind": kind, "sent": sent}


async def send_due_digests(
    session: AsyncSession,
    redis: Redis,
    sender: EmailSender | None,
    settings: AppSettings,
    now: datetime,
    *,
    public_url: str,
    email_to: str | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("daily", "weekly"):
        period = due_period(kind, settings, now)
        if period is None:
            continue
        key = f"digest:{kind}:{period}"
        if await redis.exists(key):
            continue
        try:
            out[kind] = await send_digest(
                session, sender, kind, now, public_url=public_url, email_to=email_to
            )
        except EmailError as exc:
            out[kind] = {"failed": str(exc)}  # retried on the next tick
            log.warning("alerts.digest_failed", kind=kind, error=str(exc))
            continue
        await redis.set(key, "1", ex=8 * 24 * 3600)
    return out

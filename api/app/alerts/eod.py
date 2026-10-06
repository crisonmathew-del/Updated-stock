"""The alerts stage after the EOD scan (spec §6.8, §7.1-7.2), for the session just closed:

1. **Close confirmation.** Every provisional breakout the intraday watcher logged is settled
   by the close: a `breakout` signal from the lifecycle confirms it; otherwise a
   `breakout_rejected` signal records why (closed back below the pivot, light volume, weak
   close). Either way an alert says so.
2. **Signals → alerts.** Every signal of the session that has not alerted yet becomes an alert
   (the grade filter and dedupe are the engine's). Re-running a session alerts nothing twice.
3. **Saved screens promoted to alerts**: stocks that newly match (app.alerts.screens).
4. **Holdings (sell rules, spec §6.6).** For each open position at the close: at or below the
   stop, a close below the 50-day SMA (high) or the 21-day EMA (on the day it crosses), earnings
   within `earnings_warning_days`, time to raise the stop to breakeven, the profit-taking zone.
   Each warning fires once per position (the stop once per stop level, earnings once per
   report).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.engine import AlertDraft, EmailRoute, raise_alerts
from app.alerts.screens import screen_drafts
from app.core.calendar import previous_session, sessions_between
from app.core.logging import get_logger
from app.fundamentals.earnings import estimate_next_release
from app.models import Alert, DailyBar, EarningsEvent, Holding, IndicatorDaily, Signal, Ticker
from app.scanner.evaluate import SIGNAL_LABELS
from app.settings.schema import AppSettings

log = get_logger(__name__)

HIGH = frozenset({"breakout", "breakout_rejected", "failed", "regime_change"})
TITLES = {
    "breakout": "{s} breakout confirmed",
    "breakout_rejected": "{s} breakout rejected",
    "near_pivot": "{s} is near its pivot",
    "new_top_setup": "New {g} setup: {s}",
    "extended": "{s} is extended past its buy zone",
    "failed": "{s} breakout failed",
    "invalidated": "{s} setup invalidated",
    "pocket_pivot": "{s} pocket pivot",
    "earnings_gap": "{s} gapped on earnings",
    "rs_new_high_ahead": "{s}: RS line at a new high ahead of price",
    "pullback": "{s} pullback buy point",
    "undercut_rally": "{s} undercut & rally",
    "regime_change": "Market regime change",
}


def _plan_payload(signal: Signal) -> dict[str, Any]:
    plan = (signal.context or {}).get("trade_plan") or {}
    zone = plan.get("buy_zone") or [None, None]
    return {
        "price": signal.price,
        "pivot": signal.pivot,
        "buy_zone_top": zone[1] if len(zone) > 1 else None,
        "entry": signal.entry,
        "stop": signal.stop,
        "shares": plan.get("shares"),
        "score": signal.score,
        "grade": signal.grade,
        "signal_type": signal.type,
    }


async def confirm_provisional(session: AsyncSession, day: date) -> dict[str, int]:
    """Settle the session's provisional breakouts; inserts the missing rejections."""
    rows = await session.execute(
        select(Signal).where(Signal.date == day, Signal.type == "breakout_provisional")
    )
    provisional = list(rows.scalars())
    if not provisional:
        return {}
    ids = [p.ticker_id for p in provisional]
    settled = await session.execute(
        select(Signal.ticker_id, Signal.type).where(
            Signal.date == day,
            Signal.type.in_(("breakout", "breakout_rejected")),
            Signal.ticker_id.in_(ids),
        )
    )
    outcome: dict[int | None, set[str]] = defaultdict(set)
    for tid, kind in settled.all():
        outcome[tid].add(kind)
    closes = await session.execute(
        select(DailyBar.ticker_id, DailyBar.high, DailyBar.low, DailyBar.close).where(
            DailyBar.date == day, DailyBar.ticker_id.in_(ids)
        )
    )
    bars = {int(t): (float(h), float(lo), float(c)) for t, h, lo, c in closes.all()}
    counts = {"confirmed": 0, "rejected": 0, "waiting": 0}
    for p in provisional:
        if "breakout" in outcome[p.ticker_id]:
            counts["confirmed"] += 1
            continue
        if "breakout_rejected" in outcome[p.ticker_id]:
            counts["rejected"] += 1
            continue
        if p.ticker_id not in bars:
            counts["waiting"] += 1  # the day's bar isn't in yet
            continue
        high, low, close = bars[p.ticker_id]
        pivot = p.pivot or 0.0
        if close <= pivot:
            why = (
                f"It traded above the pivot {pivot:,.2f} during the day (provisional at "
                f"{p.price:,.2f}) but closed back below it at {close:,.2f}."
            )
        else:
            why = (
                f"It closed at {close:,.2f}, above the pivot {pivot:,.2f}, but the setup did not "
                "qualify as a breakout at the close."
            )
        await session.execute(
            insert(Signal)
            .values(
                date=day,
                type="breakout_rejected",
                ticker_id=p.ticker_id,
                setup_id=p.setup_id,
                summary=f"Breakout rejected: {why}",
                price=close,
                pivot=p.pivot,
                entry=p.entry,
                stop=p.stop,
                score=p.score,
                grade=p.grade,
                context={
                    **(p.context or {}),
                    "provisional_signal_id": p.id,
                    "close": close,
                    "high": high,
                    "low": low,
                },
            )
            .on_conflict_do_nothing(index_elements=["date", "type", "ticker_id"])
        )
        counts["rejected"] += 1
    await session.commit()
    log.info("alerts.close_confirmation", day=day, **counts)
    return counts


async def signal_drafts(session: AsyncSession, day: date) -> list[AlertDraft]:
    """An alert draft for every signal of `day` that has not alerted yet (provisional
    breakouts alerted live)."""
    rows = await session.execute(
        select(Signal, Ticker.symbol)
        .outerjoin(Ticker, Ticker.id == Signal.ticker_id)
        .where(
            Signal.date == day,
            Signal.type != "breakout_provisional",
            ~exists().where(Alert.signal_id == Signal.id),
        )
        .order_by(Signal.id)
    )
    signals = rows.all()
    provisional = set(
        await session.scalars(
            select(Signal.ticker_id).where(
                Signal.date == day, Signal.type == "breakout_provisional"
            )
        )
    )
    drafts = []
    for signal, symbol in signals:
        template = TITLES.get(signal.type, "{s}: " + SIGNAL_LABELS.get(signal.type, signal.type))
        title = template.format(s=symbol or "", g=signal.grade or "")
        if signal.type in ("breakout", "breakout_rejected") and signal.ticker_id in provisional:
            title += " at the close"
        drafts.append(
            AlertDraft(
                kind=signal.type,
                priority="high" if signal.type in HIGH else "normal",
                title=title,
                body=signal.summary,
                session_date=day,
                dedupe_key=f"signal:{signal.id}",
                symbol=symbol,
                ticker_id=signal.ticker_id,
                grade=signal.grade,
                payload=_plan_payload(signal),
                signal_id=signal.id,
            )
        )
    return drafts


def _next_earnings(reported: list[date], day: date) -> tuple[date, int] | None:
    estimate = estimate_next_release(reported, day)
    if estimate is None or estimate <= day:
        return None
    return estimate, len(sessions_between(day, estimate)) - 1


@dataclass(frozen=True)
class HoldingDay:
    """An open position at a session's close, with the previous close for crossings."""

    symbol: str
    entry: float
    stop: float
    initial_stop: float
    close: float
    ema21: float | None = None
    sma50: float | None = None
    prev_close: float | None = None
    prev_ema21: float | None = None
    prev_sma50: float | None = None
    next_earnings: tuple[date, int] | None = None  # expected date, sessions away


@dataclass(frozen=True)
class SellWarning:
    rule: str
    priority: str
    title: str
    body: str
    key: str  # fires once per position and key ("" = once per position)


def _crossed_below(
    close: float, line: float | None, prev: float | None, prev_line: float | None
) -> bool:
    """Below the line at this close, and at or above it (or unknown) at the previous one."""
    if line is None or close >= line:
        return False
    return prev is None or prev_line is None or prev >= prev_line


def sell_warnings(h: HoldingDay, settings: AppSettings, day: date) -> list[SellWarning]:
    """The sell-rule warnings for one position at `day`'s close (pure)."""
    risk = h.entry - h.initial_stop
    r = (h.close - h.entry) / risk if risk > 0 else None
    gain = (h.close / h.entry - 1) * 100
    r_text = f"{r:+.1f}R, " if r is not None else ""
    where = (
        f"{h.symbol} closed at {h.close:,.2f} ({r_text}{gain:+.1f}% from your {h.entry:,.2f} entry)"
    )
    out: list[SellWarning] = []
    if h.close <= h.stop:
        out.append(
            SellWarning(
                "stop",
                "high",
                f"{h.symbol} closed at or below your stop",
                f"{where}, at or below your stop {h.stop:,.2f}. Your plan says sell.",
                f"{h.stop:.2f}",
            )
        )
    elif h.sma50 is not None and _crossed_below(h.close, h.sma50, h.prev_close, h.prev_sma50):
        out.append(
            SellWarning(
                "below_50",
                "high",
                f"{h.symbol} closed below its 50-day line",
                f"{where}, below the 50-day SMA {h.sma50:,.2f}: a standard sell signal for a "
                "position.",
                day.isoformat(),
            )
        )
    elif h.ema21 is not None and _crossed_below(h.close, h.ema21, h.prev_close, h.prev_ema21):
        out.append(
            SellWarning(
                "below_21",
                "normal",
                f"{h.symbol} closed below its 21-day EMA",
                f"{where}, below the 21-day EMA {h.ema21:,.2f}: the aggressive trailing stop.",
                day.isoformat(),
            )
        )
    breakeven = (r is not None and r >= settings.breakeven_after_r) or (
        gain >= settings.breakeven_after_gain_pct
    )
    if breakeven and h.stop < h.entry:
        out.append(
            SellWarning(
                "breakeven",
                "normal",
                f"Raise your {h.symbol} stop to breakeven",
                f"{where}. Your stop {h.stop:,.2f} is still below the entry: the plan raises it "
                "to breakeven now.",
                "",
            )
        )
    if settings.profit_take_min_pct <= gain <= settings.profit_take_max_pct:
        out.append(
            SellWarning(
                "profit_zone",
                "normal",
                f"{h.symbol} is in the profit-taking zone",
                f"{where}: inside the {settings.profit_take_min_pct:g}-"
                f"{settings.profit_take_max_pct:g}% zone where the plan takes partial profits.",
                "",
            )
        )
    if h.next_earnings is not None and h.next_earnings[1] <= settings.earnings_warning_days:
        when, away = h.next_earnings
        out.append(
            SellWarning(
                "earnings",
                "normal",
                f"{h.symbol} reports earnings soon",
                f"Expected {when:%a %b %-d} ({away} session{'s' if away != 1 else ''} away, "
                f"estimated). {where}. Decide whether to hold through the report.",
                when.isoformat(),
            )
        )
    return out


async def holding_drafts(
    session: AsyncSession, day: date, settings: AppSettings
) -> list[AlertDraft]:
    """Sell-rule warnings for open positions, judged on `day`'s close."""
    rows = await session.execute(
        select(Holding, Ticker.symbol, DailyBar.close, IndicatorDaily.ema21, IndicatorDaily.sma50)
        .join(Ticker, Ticker.id == Holding.ticker_id)
        .join(DailyBar, (DailyBar.ticker_id == Holding.ticker_id) & (DailyBar.date == day))
        .outerjoin(
            IndicatorDaily,
            (IndicatorDaily.ticker_id == Holding.ticker_id) & (IndicatorDaily.date == day),
        )
        .where(Holding.closed_on.is_(None), Holding.opened_on <= day)
    )
    held = rows.all()
    if not held:
        return []
    tickers = [h.ticker_id for h, *_ in held]
    prior_rows = await session.execute(
        select(DailyBar.ticker_id, DailyBar.close, IndicatorDaily.ema21, IndicatorDaily.sma50)
        .outerjoin(
            IndicatorDaily,
            (IndicatorDaily.ticker_id == DailyBar.ticker_id)
            & (IndicatorDaily.date == DailyBar.date),
        )
        .where(DailyBar.date == previous_session(day), DailyBar.ticker_id.in_(tickers))
    )
    prior = {int(t): (float(c), e21, s50) for t, c, e21, s50 in prior_rows.all()}
    reported: dict[int, list[date]] = defaultdict(list)
    releases = await session.execute(
        select(EarningsEvent.ticker_id, EarningsEvent.report_date).where(
            EarningsEvent.ticker_id.in_(tickers),
            EarningsEvent.status == "reported",
            EarningsEvent.report_date <= day,
        )
    )
    for tid, report in releases.all():
        reported[int(tid)].append(report)
    sent = set(
        await session.scalars(
            select(Alert.dedupe_key).where(Alert.holding_id.in_([h.id for h, *_ in held]))
        )
    )

    drafts: list[AlertDraft] = []
    for h, symbol, close, ema21, sma50 in held:
        prev_close, prev_ema21, prev_sma50 = prior.get(h.ticker_id, (None, None, None))
        position = HoldingDay(
            symbol=symbol,
            entry=h.entry_price,
            stop=h.stop,
            initial_stop=h.initial_stop,
            close=float(close),
            ema21=ema21,
            sma50=sma50,
            prev_close=prev_close,
            prev_ema21=prev_ema21,
            prev_sma50=prev_sma50,
            next_earnings=_next_earnings(reported.get(h.ticker_id, []), day),
        )
        risk = h.entry_price - h.initial_stop
        payload: dict[str, Any] = {
            "price": position.close,
            "entry": h.entry_price,
            "stop": h.stop,
            "r": (position.close - h.entry_price) / risk if risk > 0 else None,
            "gain_pct": (position.close / h.entry_price - 1) * 100,
        }
        for w in sell_warnings(position, settings, day):
            key = f"holding:{h.id}:{w.rule}" + (f":{w.key}" if w.key else "")
            if key in sent:
                continue
            drafts.append(
                AlertDraft(
                    kind=f"holding_{w.rule}",
                    priority=w.priority,
                    title=w.title,
                    body=w.body,
                    session_date=day,
                    dedupe_key=key,
                    symbol=symbol,
                    ticker_id=h.ticker_id,
                    user_id=h.user_id,
                    payload={**payload, "rule": w.rule},
                    holding_id=h.id,
                )
            )
    return drafts


async def session_alerts(
    session: AsyncSession,
    redis: Redis,
    settings: AppSettings,
    day: date,
    now: datetime,
    route: EmailRoute,
) -> tuple[dict[str, Any], list[int]]:
    """Close confirmation, then alerts for the session's signals and holdings. Returns stats
    and the ids of alerts to email now."""
    confirmation = await confirm_provisional(session, day)
    drafts = [
        *await signal_drafts(session, day),
        *await holding_drafts(session, day, settings),
        *await screen_drafts(session, redis, day),
    ]
    alerts = await raise_alerts(session, redis, drafts, settings, now, route)
    queued = [a.id for a in alerts if a.delivery.get("email") == "queued"]
    stats = {
        "session": day.isoformat(),
        "provisional": confirmation,
        "drafts": len(drafts),
        "alerts": len(alerts),
        "emails_queued": len(queued),
    }
    return stats, queued

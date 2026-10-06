"""The intraday watcher's rules (spec §6.8, §7.1-7.2): what the latest print means for a
watched stock. Pure: the streamer keeps each stock's `DayState`, calls `evaluate` after every
print and hands the events to the alerts engine.

Rules, all on regular-session prices (thin pre-market prints never trigger them):
- **Provisional breakout** (high): a basing / near-pivot setup trades above its pivot, inside
  the buy zone, with projected volume ≥ `breakout_volume_min_pct_of_avg` (≥ the "strong" level
  is called strong), once `intraday_projection_min_minutes` have passed. Volume is projected
  with the time-of-day curve; a partial (IEX) feed is scaled up by its share of the market and
  said so. It stays provisional: the close confirms or rejects it.
- **Past the buy zone** (normal): the setup trades above the top of its buy zone before (or
  after) breaking out: extended, don't chase.
- **Stop hit** (high): a broken-out setup trades at or below its plan's stop, or a holding at
  or below its stop.
- **Your rules**: price or a moving average crossed (from the previous print, or the previous
  close at the first print), change on the day, projected volume × average.
Each event fires once per session (`fired` keys: per setup, holding, or rule and stock); the
alerts engine adds its cooldown.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.intraday.day import DayState
from app.intraday.session import Phase, minutes_elapsed, phase
from app.intraday.volume import VolumeCurve
from app.settings.schema import AppSettings

PRE_BREAKOUT = ("basing", "near_pivot")
MA_LABELS = {
    "ema10": "10-day EMA",
    "ema21": "21-day EMA",
    "sma50": "50-day SMA",
    "sma150": "150-day SMA",
    "sma200": "200-day SMA",
}


@dataclass(frozen=True)
class SetupWatch:
    setup_id: int
    state: str
    pattern: str | None
    pivot: float | None
    buy_zone_top: float | None
    entry: float | None
    stop: float | None
    shares: int | None
    score: float | None
    grade: str | None


@dataclass(frozen=True)
class HoldingWatch:
    holding_id: int
    user_id: int
    entry: float
    stop: float
    initial_stop: float
    shares: int


@dataclass(frozen=True)
class RuleWatch:
    rule_id: int
    user_id: int
    name: str
    condition: str
    value: float | None
    level: float | None  # the price to cross: the value, or the moving average's level
    ma: str | None
    priority: str
    channels: tuple[str, ...]


@dataclass(frozen=True)
class WatchContext:
    symbol: str
    ticker_id: int
    name: str
    prev_close: float | None
    avg_volume: float | None  # 50-day average, consolidated shares
    setup: SetupWatch | None = None
    holdings: tuple[HoldingWatch, ...] = ()
    rules: tuple[RuleWatch, ...] = ()


@dataclass(frozen=True)
class IntradayEvent:
    kind: str
    priority: str
    symbol: str
    ticker_id: int
    ts: datetime
    price: float
    title: str
    body: str
    key: str  # fires once per session
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VolumeView:
    projected: float | None
    ratio_pct: float | None  # projected ÷ 50-day average, in %


def volume_view(
    ctx: WatchContext, day: DayState, ts: datetime, curve: VolumeCurve, volume_share: float
) -> VolumeView:
    projected = curve.project(day.volume / volume_share, ts)
    if projected is None or not ctx.avg_volume:
        return VolumeView(projected, None)
    return VolumeView(projected, projected / ctx.avg_volume * 100)


def _p(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def evaluate(
    ctx: WatchContext,
    day: DayState,
    *,
    curve: VolumeCurve,
    settings: AppSettings,
    fired: set[str],
    volume_share: float = 1.0,
    partial_volume: bool = False,
) -> list[IntradayEvent]:
    price, ts = day.last, day.last_ts
    if price is None or ts is None or phase(ts) is not Phase.REGULAR:
        return []
    events: list[IntradayEvent] = []
    view = volume_view(ctx, day, ts, curve, volume_share)
    change = None if not ctx.prev_close else (price / ctx.prev_close - 1) * 100
    base = {
        "price": price,
        "change_pct": change,
        "volume": day.volume,
        "projected_volume": view.projected,
        "volume_ratio_pct": view.ratio_pct,
        "partial_volume": partial_volume,
    }
    projection_ready = minutes_elapsed(ts) >= settings.intraday_projection_min_minutes

    def emit(kind: str, priority: str, key: str, title: str, body: str, **data: Any) -> None:
        if key in fired:
            return
        fired.add(key)
        events.append(
            IntradayEvent(
                kind,
                priority,
                ctx.symbol,
                ctx.ticker_id,
                ts,
                price,
                title,
                body,
                key,
                {**base, **data},
            )
        )

    setup = ctx.setup
    if setup is not None and setup.pivot is not None:
        plan = {
            "setup_id": setup.setup_id,
            "pivot": setup.pivot,
            "buy_zone_top": setup.buy_zone_top,
            "entry": setup.entry,
            "stop": setup.stop,
            "shares": setup.shares,
            "score": setup.score,
            "grade": setup.grade,
            "pattern": setup.pattern,
        }
        broke_out = setup.state == "breakout" or f"breakout:{setup.setup_id}" in fired
        above_zone = setup.buy_zone_top is not None and price > setup.buy_zone_top
        if above_zone:
            pct = (price / setup.pivot - 1) * 100
            emit(
                "breakout_extended",
                "normal",
                f"extended:{setup.setup_id}",
                f"{ctx.symbol} is past its buy zone",
                f"{_p(price)} is {pct:.1f}% above the {_p(setup.pivot)} pivot, beyond the buy "
                f"zone (up to {_p(setup.buy_zone_top)}). Extended: don't chase; wait for a "
                "pullback or a new base.",
                **plan,
            )
        elif (
            setup.state in PRE_BREAKOUT
            and not broke_out
            and price > setup.pivot
            and projection_ready
            and view.ratio_pct is not None
            and view.ratio_pct >= settings.breakout_volume_min_pct_of_avg
        ):
            strong = view.ratio_pct >= settings.breakout_volume_strong_pct_of_avg
            feed = " IEX volume scaled to the whole market:" if partial_volume else ""
            plan_text = (
                f" Plan: entry {_p(setup.entry)}, stop {_p(setup.stop)}"
                + (f", {setup.shares:,} shares." if setup.shares else ".")
                if setup.entry is not None
                else ""
            )
            emit(
                "breakout_provisional",
                "high",
                f"breakout:{setup.setup_id}",
                f"{ctx.symbol} breaking out{' strongly' if strong else ''} (provisional)",
                f"{_p(price)} is above the {_p(setup.pivot)} pivot on projected volume of "
                f"{view.ratio_pct:.0f}% of average{' (strong)' if strong else ''}.{feed}"
                f"{plan_text} Provisional until the close: it needs a close in the top third "
                "of the day's range on enough volume.",
                strong=strong,
                **plan,
            )
            broke_out = True
        if broke_out and setup.stop is not None and price <= setup.stop:
            emit(
                "setup_stop",
                "high",
                f"setup_stop:{setup.setup_id}",
                f"{ctx.symbol} hit its stop",
                f"{_p(price)} is at or below the plan's stop {_p(setup.stop)}: the breakout "
                "has failed.",
                **plan,
            )

    for h in ctx.holdings:
        if price <= h.stop:
            risk = h.entry - h.initial_stop
            r = (price - h.entry) / risk if risk > 0 else None
            pnl = (price - h.entry) * h.shares
            r_text = f" ({r:+.1f}R, {pnl:+,.0f})" if r is not None else ""
            emit(
                "holding_stop",
                "high",
                f"holding_stop:{h.holding_id}",
                f"Stop hit on your {ctx.symbol} position",
                f"{_p(price)} is at or below your stop {_p(h.stop)}{r_text}. Your plan says sell.",
                holding_id=h.holding_id,
                user_id=h.user_id,
                entry=h.entry,
                stop=h.stop,
                r=r,
            )

    prior = day.previous if day.previous is not None else ctx.prev_close
    for rule in ctx.rules:
        hit = _rule_hit(rule, price, prior, change, view, projection_ready)
        if hit is None:
            continue
        emit(
            "rule",
            rule.priority,
            f"rule:{rule.rule_id}:{ctx.symbol}",
            f"{ctx.symbol}: {rule.name}",
            hit,
            rule_id=rule.rule_id,
            user_id=rule.user_id,
            channels=list(rule.channels),
        )
    return events


def _rule_hit(
    rule: RuleWatch,
    price: float,
    prior: float | None,
    change: float | None,
    view: VolumeView,
    projection_ready: bool,
) -> str | None:
    """The sentence describing why `rule` fired on this print, or None."""
    c, level, value = rule.condition, rule.level, rule.value
    what = f"the {MA_LABELS.get(rule.ma or '', rule.ma)}" if rule.ma else _p(level)
    if c in ("price_above", "ma_cross_above"):
        crossed = level is not None and price >= level and (prior is None or prior < level)
        return f"Crossed above {what} ({_p(level)}): now {_p(price)}." if crossed else None
    if c in ("price_below", "ma_cross_below"):
        crossed = level is not None and price <= level and (prior is None or prior > level)
        return f"Crossed below {what} ({_p(level)}): now {_p(price)}." if crossed else None
    if value is None:
        return None
    if c == "change_above" and change is not None and change >= value:
        return f"Up {change:.1f}% on the day (your threshold {value:+.1f}%)."
    if c == "change_below" and change is not None and change <= value:
        return f"Down {abs(change):.1f}% on the day (your threshold {value:+.1f}%)."
    ratio = None if view.ratio_pct is None else view.ratio_pct / 100
    if c == "volume_ratio_above" and projection_ready and ratio is not None and ratio >= value:
        return f"Projected volume {ratio:.1f}× the 50-day average (your threshold {value:.1f}×)."
    return None

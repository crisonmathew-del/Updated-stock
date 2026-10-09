"""What the streamer watches (spec §6.8): the stocks it streams, each with everything the
intraday rules need, and the wider universe the pre-market scan and the sweep check.

Streamed, in this order until `stream_max_symbols` is reached (Alpaca's free plan allows 30):
1. open holdings (their stops and sell rules),
2. the market indexes SPY, QQQ and IWM (the top bar and the dashboard's market card),
3. near-pivot setups, closest to the pivot first,
4. broken-out setups (their stops, and extension past the buy zone),
5. stocks named by enabled alert rules (ticker, watchlist and holdings scopes),
6. basing setups, closest first,
7. watchlist stocks.
The scan universe adds every active setup and the Stage 2 leaders (RS Rating ≥ 80); the
indexes are streamed for their prices only, never scanned.
Levels come from the latest close and indicators: nothing intraday is invented.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intraday.live import INDEX_SYMBOLS
from app.models import (
    AlertRule,
    DailyBar,
    EarningsEvent,
    Holding,
    IndicatorDaily,
    Setup,
    Ticker,
    Watchlist,
    WatchlistItem,
)
from app.scanner.intraday_scan import (
    HoldingWatch,
    RuleWatch,
    SetupWatch,
    WatchContext,
)
from app.scanner.live_scans import Candidate
from app.settings.schema import AppSettings

STREAMED_SETUP_STATES = ("near_pivot", "breakout", "basing")
INTRADAY_CONDITIONS = frozenset(
    {
        "price_above",
        "price_below",
        "ma_cross_above",
        "ma_cross_below",
        "change_above",
        "change_below",
        "volume_ratio_above",
    }
)
LEADER_MIN_RS = 80
MAX_UNIVERSE = 1500
MA_COLUMNS = ("ema10", "ema21", "sma50", "sma150", "sma200")


@dataclass
class WatchPlan:
    contexts: dict[str, WatchContext] = field(default_factory=dict)  # streamed, by symbol
    reasons: dict[str, str] = field(default_factory=dict)
    universe: dict[str, Candidate] = field(default_factory=dict)  # the scans' universe
    as_of: date | None = None  # the session the levels come from

    @property
    def symbols(self) -> list[str]:
        return list(self.contexts)


def _plan_numbers(setup: Setup) -> tuple[float | None, float | None, float | None, int | None]:
    plan = setup.trade_plan or {}
    zone = plan.get("buy_zone") or [None, None]
    return (
        zone[1] if len(zone) > 1 else None,
        plan.get("entry"),
        plan.get("stop"),
        plan.get("shares"),
    )


async def load_plan(session: AsyncSession, settings: AppSettings, today: date) -> WatchPlan:
    """The watch plan for the session `today` (levels as of the latest stored session)."""
    as_of = await session.scalar(
        select(func.max(IndicatorDaily.date)).where(IndicatorDaily.date < today)
    )
    ordered: list[tuple[int, str]] = []  # (ticker_id, reason), in priority order

    holdings = (
        await session.execute(
            select(Holding).where(Holding.closed_on.is_(None)).order_by(Holding.opened_on)
        )
    ).scalars()
    held: dict[int, list[HoldingWatch]] = defaultdict(list)
    held_by_user: dict[int, set[int]] = defaultdict(set)
    for h in holdings:
        held[h.ticker_id].append(
            HoldingWatch(h.id, h.user_id, h.entry_price, h.stop, h.initial_stop, h.shares)
        )
        held_by_user[h.user_id].add(h.ticker_id)
        ordered.append((h.ticker_id, "holding"))

    indexes = {
        symbol: int(tid)
        for tid, symbol in (
            await session.execute(
                select(Ticker.id, Ticker.symbol).where(
                    Ticker.symbol.in_(INDEX_SYMBOLS), Ticker.is_benchmark, Ticker.active
                )
            )
        ).all()
    }
    index_ids = set(indexes.values())
    ordered += [(indexes[s], "market index") for s in INDEX_SYMBOLS if s in indexes]

    setups = (
        await session.execute(
            select(Setup).where(Setup.active, Setup.state.in_(STREAMED_SETUP_STATES))
        )
    ).scalars()
    by_state: dict[str, list[Setup]] = defaultdict(list)
    setup_of: dict[int, Setup] = {}
    for s in setups:
        by_state[s.state].append(s)
        setup_of[s.ticker_id] = s

    def closest(rows: list[Setup]) -> list[Setup]:
        return sorted(rows, key=lambda s: (abs(s.readiness_pct or 0.0), -s.score))

    ordered += [(s.ticker_id, "near pivot") for s in closest(by_state["near_pivot"])]
    ordered += [
        (s.ticker_id, "broken out") for s in sorted(by_state["breakout"], key=lambda s: -s.score)
    ]

    rules = list(
        (
            await session.execute(
                select(AlertRule).where(
                    AlertRule.enabled, AlertRule.condition.in_(INTRADAY_CONDITIONS)
                )
            )
        ).scalars()
    )
    watch_items = (
        await session.execute(
            select(Watchlist.id, Watchlist.user_id, WatchlistItem.ticker_id)
            .join(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
            .order_by(Watchlist.position, WatchlistItem.position)
        )
    ).all()
    lists: dict[int, list[int]] = defaultdict(list)
    for wid, _, tid in watch_items:
        lists[int(wid)].append(int(tid))
    rule_targets: dict[int, list[AlertRule]] = defaultdict(list)
    for rule in rules:
        if rule.scope == "ticker" and rule.ticker_id is not None:
            targets = [rule.ticker_id]
        elif rule.scope == "watchlist" and rule.watchlist_id is not None:
            targets = lists.get(rule.watchlist_id, [])
        elif rule.scope == "holdings":
            targets = sorted(held_by_user.get(rule.user_id, set()))
        else:
            targets = []
        for tid in targets:
            rule_targets[tid].append(rule)
            ordered.append((tid, "alert rule"))
    ordered += [(s.ticker_id, "basing") for s in closest(by_state["basing"])]
    ordered += [(int(tid), "watchlist") for _, _, tid in watch_items]

    chosen: dict[int, str] = {}
    for tid, reason in ordered:
        if tid not in chosen and len(chosen) < settings.stream_max_symbols:
            chosen[tid] = reason

    # The scans' universe: everything above, every active setup and the Stage 2 leaders.
    universe_ids = set(chosen) | set(setup_of) | {int(t) for _, _, t in watch_items} | set(held)
    if as_of is not None:
        leaders = await session.scalars(
            select(IndicatorDaily.ticker_id)
            .where(
                IndicatorDaily.date == as_of,
                IndicatorDaily.stage == 2,
                IndicatorDaily.rs_rating >= LEADER_MIN_RS,
            )
            .order_by(IndicatorDaily.rs_rating.desc())
            .limit(MAX_UNIVERSE)
        )
        universe_ids |= {int(t) for t in leaders}
    if not universe_ids:
        return WatchPlan(as_of=as_of)

    columns = [getattr(IndicatorDaily, c) for c in MA_COLUMNS]
    rows = (
        await session.execute(
            select(
                Ticker.id,
                Ticker.symbol,
                Ticker.name,
                DailyBar.close,
                IndicatorDaily.avg_volume_50,
                *columns,
            )
            .outerjoin(DailyBar, and_(DailyBar.ticker_id == Ticker.id, DailyBar.date == as_of))
            .outerjoin(
                IndicatorDaily,
                and_(IndicatorDaily.ticker_id == Ticker.id, IndicatorDaily.date == as_of),
            )
            .where(Ticker.id.in_(universe_ids), Ticker.active)
        )
    ).all()
    reported = set()
    if as_of is not None:
        # Results after the last close or before today's open: an earnings reaction today.
        reported = set(
            await session.scalars(
                select(EarningsEvent.ticker_id).where(
                    EarningsEvent.ticker_id.in_(universe_ids),
                    EarningsEvent.status == "reported",
                    or_(
                        and_(
                            EarningsEvent.report_date == as_of,
                            EarningsEvent.timing.in_(("after_close", "unknown")),
                        ),
                        and_(
                            EarningsEvent.report_date > as_of,
                            EarningsEvent.report_date <= today,
                        ),
                    ),
                )
            )
        )

    plan = WatchPlan(as_of=as_of)
    info: dict[int, tuple[str, str, float | None, float | None, dict[str, float | None]]] = {}
    for tid, symbol, name, close, avg_volume, *ma_values in rows:
        info[int(tid)] = (
            symbol,
            name,
            None if close is None else float(close),
            avg_volume,
            dict(zip(MA_COLUMNS, ma_values, strict=True)),
        )
        if int(tid) in index_ids:
            continue
        found = setup_of.get(int(tid))
        plan.universe[symbol] = Candidate(
            symbol,
            int(tid),
            name,
            None if close is None else float(close),
            avg_volume,
            found.grade if found else None,
            found.state if found else None,
            int(tid) in reported,
        )

    for tid, reason in chosen.items():
        if tid not in info:
            continue
        symbol, name, close, avg_volume, levels = info[tid]
        active = setup_of.get(tid)
        setup = None
        if active is not None:
            zone_top, entry, stop, shares = _plan_numbers(active)
            setup = SetupWatch(
                active.id,
                active.state,
                active.pattern_type,
                active.pivot,
                zone_top,
                entry,
                stop,
                shares,
                active.score,
                active.grade,
            )
        watches = []
        for rule in rule_targets.get(tid, []):
            level = rule.value if rule.condition in ("price_above", "price_below") else None
            if rule.condition.startswith("ma_cross"):
                level = levels.get(rule.ma or "")
                if level is None:
                    continue  # no moving average yet: nothing to cross
            watches.append(
                RuleWatch(
                    rule.id,
                    rule.user_id,
                    rule.name,
                    rule.condition,
                    rule.value,
                    level,
                    rule.ma,
                    rule.priority,
                    tuple(rule.channels or ()),
                )
            )
        plan.contexts[symbol] = WatchContext(
            symbol=symbol,
            ticker_id=tid,
            name=name,
            prev_close=close,
            avg_volume=avg_volume,
            setup=setup,
            holdings=tuple(held.get(tid, ())),
            rules=tuple(watches),
        )
        plan.reasons[symbol] = reason
    return plan

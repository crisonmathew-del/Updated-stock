"""The streamer's engine (spec §6.8, §7.1): one live feed in, alerts and live updates out.

For every print of a watched stock it updates the stock's `DayState`, records completed
minute bars, runs the intraday rules (app.scanner.intraday_scan) and hands what they find to
the alerts engine, which records, pushes and emails it. A provisional breakout is also logged
as a `breakout_provisional` signal; the close confirms or rejects it (app.alerts.eod).

Background loops: quotes are pushed to open pages at most every 250 ms (only the stocks that
traded), minute bars are written every 5 seconds, and the watch plan is reloaded every minute
or when the API asks (`watch:refresh`: a rule or holding changed). The pre-market scan and the
intraday sweep run on the feed's clock, so a replay behaves like a live day; each also gives
every scanned stock that isn't streamed its price at that moment (`"source": "check"`).

At the end of a replay with `replay_close`, the day's daily bars come from the recording (when
not stored yet) and the EOD analytics and alerts run for that session.
"""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.delivery import send_alert_emails
from app.alerts.email import EmailSender
from app.alerts.engine import LIVE_CHANNEL, AlertDraft, EmailRoute, publish, raise_alerts
from app.alerts.jobs import eod_alerts
from app.core.config import Settings
from app.core.jobs import job_lock
from app.core.logging import get_logger
from app.data.bars import upsert_bars
from app.intraday.day import DayState
from app.intraday.live import EVENTS_KEY, MAX_EVENTS, QUOTES_KEY, REFRESH_CHANNEL, scan_key
from app.intraday.plan import WatchPlan, load_plan
from app.intraday.session import Phase, in_window, minutes_elapsed, phase, session_date
from app.intraday.store import current_curve, load_bars, save_bars
from app.intraday.volume import STANDARD_CURVE, VolumeCurve
from app.models import DailyBar, Signal, Ticker
from app.providers.base import MinuteBar, PriceHistory, Snapshot, StreamProvider, Trade
from app.providers.replay import ReplayStream, daily_bars, ticks_from_bar
from app.scanner.eod_scan import run_analytics
from app.scanner.intraday_scan import IntradayEvent, evaluate
from app.scanner.live_scans import ScanHit, premarket_hits, sweep_hits
from app.settings import store
from app.settings.schema import AppSettings

log = get_logger(__name__)

QUOTE_INTERVAL_SECONDS = 0.25
BAR_FLUSH_SECONDS = 5.0
REFRESH_SECONDS = 60.0
PREMARKET_WINDOW = ("08:00", "09:25")
PREMARKET_EVERY = timedelta(minutes=5)
SWEEP_EVERY = timedelta(minutes=15)
INGEST_LOCK = "ingest"
# Intraday events about a setup (not a user's holding or rule): pushed to every open page.
SETUP_EVENTS = frozenset({"breakout_provisional", "breakout_extended", "setup_stop"})


@dataclass(frozen=True)
class Batch:
    events: list[IntradayEvent]
    received_at: datetime  # wall clock when the triggering print arrived


def event_draft(event: IntradayEvent, received_at: datetime, signal_id: int | None) -> AlertDraft:
    day = session_date(event.ts)
    data = event.data
    channels = tuple(data.get("channels") or ()) or None
    return AlertDraft(
        kind=event.kind,
        priority=event.priority,
        title=event.title,
        body=event.body,
        session_date=day,
        dedupe_key=f"{event.key}:{day.isoformat()}",
        symbol=event.symbol,
        ticker_id=event.ticker_id,
        user_id=data.get("user_id"),
        grade=data.get("grade"),
        payload={
            **{k: v for k, v in data.items() if k not in ("channels", "user_id")},
            "intraday": True,
            "at": event.ts.isoformat(),
            "received_at": received_at.isoformat(),
        },
        signal_id=signal_id,
        rule_id=data.get("rule_id") if event.kind == "rule" else None,
        holding_id=data.get("holding_id"),
        channels=channels,
    )


def hit_draft(hit: ScanHit, day: date) -> AlertDraft:
    return AlertDraft(
        kind="premarket_gap" if hit.scan == "premarket" else "sweep",
        priority="normal",
        title=hit.title(),
        body=hit.body(),
        session_date=day,
        dedupe_key=f"{hit.scan}:{hit.symbol}:{day.isoformat()}",
        symbol=hit.symbol,
        ticker_id=hit.ticker_id,
        grade=hit.grade,
        payload={**hit.as_json(), "price": hit.price, "intraday": True},
    )


class Watcher:
    def __init__(
        self,
        feed: StreamProvider,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        config: Settings,
        sender: EmailSender | None,
        route: EmailRoute,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.feed = feed
        self._sessions = sessionmaker
        self.redis = redis
        self.config = config
        self.sender = sender
        self.route = route
        self.wall = wall
        self.settings = AppSettings()
        self.plan = WatchPlan()
        self.curve: VolumeCurve = STANDARD_CURVE
        self.day: date | None = None
        self.days: dict[str, DayState] = {}
        self.fired: set[str] = set()
        self.pending_bars: list[MinuteBar] = []
        self.dirty: set[str] = set()
        self.queue: asyncio.Queue[Batch] = asyncio.Queue()
        self.stats: dict[str, int] = {"trades": 0, "events": 0, "alerts": 0, "bars": 0}
        self._last_premarket: datetime | None = None
        self._last_sweep: datetime | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._refresh_wanted = asyncio.Event()

    # --- state -------------------------------------------------------------------------------

    @property
    def volume_share(self) -> float:
        if self.feed.partial_volume:
            return self.settings.partial_feed_volume_share_pct / 100
        return self.feed.volume_share

    async def refresh(self) -> None:
        """Reload settings, the watch plan and the volume curve; resubscribe."""
        today = session_date(self.feed.now())
        async with self._sessions() as session:
            self.settings = await store.load(session)
            self.plan = await load_plan(session, self.settings, today)
            self.curve = await current_curve(session, self.settings)
            if today != self.day:
                await self._start_day(session, today)
        await self.feed.subscribe(self.plan.symbols)
        log.info(
            "streamer.watching",
            session=today,
            symbols=len(self.plan.symbols),
            universe=len(self.plan.universe),
            curve=self.curve.source,
        )

    async def _start_day(self, session: AsyncSession, today: date) -> None:
        """A new session: start fresh, recovering what a restart mid-day would lose."""
        self.day = today
        self.days, self.fired = {}, set()
        self._last_premarket = self._last_sweep = None
        logged = await session.execute(
            select(Signal.setup_id).where(
                Signal.date == today, Signal.type == "breakout_provisional"
            )
        )
        self.fired |= {f"breakout:{sid}" for sid in logged.scalars() if sid is not None}
        if isinstance(self.feed, ReplayStream):
            return  # the recording is replayed from its start
        for bar in await load_bars(session, today, self.plan.symbols):
            state = self.days.setdefault(bar.symbol, DayState(bar.symbol))
            for tick in ticks_from_bar(bar):
                state.add(tick)
        for state in self.days.values():
            state.flush()  # already stored

    # --- the feed ----------------------------------------------------------------------------

    async def on_trade(self, trade: Trade) -> None:
        if session_date(trade.timestamp) != self.day:
            await self.refresh()
        received = self.wall()
        state = self.days.get(trade.symbol)
        if state is None:
            state = self.days[trade.symbol] = DayState(trade.symbol)
        self.pending_bars.extend(state.add(trade))
        self.stats["trades"] += 1
        self.dirty.add(trade.symbol)
        ctx = self.plan.contexts.get(trade.symbol)
        if ctx is not None:
            events = evaluate(
                ctx,
                state,
                curve=self.curve,
                settings=self.settings,
                fired=self.fired,
                volume_share=self.volume_share,
                partial_volume=self.feed.partial_volume,
            )
            if events:
                self.stats["events"] += len(events)
                self.queue.put_nowait(Batch(events, received))
        self._maybe_scan(trade.timestamp)

    def _maybe_scan(self, now: datetime) -> None:
        if self._scan_task is not None and not self._scan_task.done():
            return
        when = phase(now)
        premarket = when is Phase.PREMARKET and in_window(now, *PREMARKET_WINDOW)
        if premarket and (
            self._last_premarket is None or now - self._last_premarket >= PREMARKET_EVERY
        ):
            self._last_premarket = now
            self._scan_task = asyncio.create_task(self._scan("premarket", now))
        sweep = when is Phase.REGULAR and minutes_elapsed(now) >= SWEEP_EVERY.total_seconds() / 60
        if sweep and (self._last_sweep is None or now - self._last_sweep >= SWEEP_EVERY):
            self._last_sweep = now
            self._scan_task = asyncio.create_task(self._scan("sweep", now))

    async def _scan(self, scan: str, now: datetime) -> None:
        try:
            snaps = await self.feed.snapshots(list(self.plan.universe))
            # Every scanned stock that isn't streamed gets this check's price.
            await self._store_quotes(
                [q for s in snaps.values() if (q := self.checked_quote(s, now)) is not None]
            )
            if scan == "premarket":
                hits = premarket_hits(
                    self.plan.universe, snaps, self.settings, now, self.volume_share
                )
            else:
                hits = sweep_hits(
                    self.plan.universe, snaps, self.settings, self.curve, now, self.volume_share
                )
            day = session_date(now)
            body = {"at": now.isoformat(), "items": [h.as_json() for h in hits]}
            for key in (scan_key(scan, day), scan_key(scan, "latest")):
                await self.redis.set(key, json.dumps(body), ex=2 * 86400)
            await publish(self.redis, {"type": "scan", "scan": scan, "data": body})
            if hits:
                await self._alert([hit_draft(h, day) for h in hits])
            log.info("streamer.scan", scan=scan, at=now, candidates=len(snaps), hits=len(hits))
        except Exception:
            log.exception("streamer.scan_failed", scan=scan)

    # --- alerts ------------------------------------------------------------------------------

    async def _alert(self, drafts: list[AlertDraft]) -> None:
        async with self._sessions() as session:
            alerts = await raise_alerts(
                session, self.redis, drafts, self.settings, self.wall(), self.route
            )
            self.stats["alerts"] += len(alerts)
            queued = [a.id for a in alerts if a.delivery.get("email") == "queued"]
            if queued:
                await send_alert_emails(
                    session,
                    self.sender,
                    queued,
                    public_url=self.config.public_url,
                    email_to=self.config.email_to,
                )

    async def _log_provisional(self, session: AsyncSession, event: IntradayEvent) -> int | None:
        d = event.data
        day = session_date(event.ts)
        inserted = await session.scalar(
            insert(Signal)
            .values(
                date=day,
                type="breakout_provisional",
                ticker_id=event.ticker_id,
                setup_id=d.get("setup_id"),
                summary=f"Breakout (provisional): {event.body}",
                price=event.price,
                pivot=d.get("pivot"),
                entry=d.get("entry"),
                stop=d.get("stop"),
                score=d.get("score"),
                grade=d.get("grade"),
                context={
                    "at": event.ts.isoformat(),
                    "projected_volume": d.get("projected_volume"),
                    "volume_ratio_pct": d.get("volume_ratio_pct"),
                    "partial_volume": d.get("partial_volume"),
                    "strong": d.get("strong"),
                    "trade_plan": {
                        "shares": d.get("shares"),
                        "buy_zone": [d.get("pivot"), d.get("buy_zone_top")],
                    },
                },
            )
            .on_conflict_do_nothing(index_elements=["date", "type", "ticker_id"])
            .returning(Signal.id)
        )
        if inserted is None:
            inserted = await session.scalar(
                select(Signal.id).where(
                    Signal.date == day,
                    Signal.type == "breakout_provisional",
                    Signal.ticker_id == event.ticker_id,
                )
            )
        await session.commit()
        return inserted

    async def _handle(self, batch: Batch) -> None:
        drafts = []
        async with self._sessions() as session:
            for event in batch.events:
                signal_id = None
                if event.kind == "breakout_provisional":
                    signal_id = await self._log_provisional(session, event)
                drafts.append(event_draft(event, batch.received_at, signal_id))
        for event in batch.events:
            if event.kind in SETUP_EVENTS:
                data = {
                    "kind": event.kind,
                    "symbol": event.symbol,
                    "setup_id": event.data.get("setup_id"),
                    "price": event.price,
                    "at": event.ts.isoformat(),
                    "title": event.title,
                }
                # Kept for pages opened later in the session (GET /api/live), and pushed now.
                await self.redis.lpush(EVENTS_KEY, json.dumps(data))  # type: ignore[misc]
                await self.redis.ltrim(EVENTS_KEY, 0, MAX_EVENTS - 1)  # type: ignore[misc]
                await publish(self.redis, {"type": "setup_event", "data": data})
        await self._alert(drafts)

    async def _alerts_loop(self) -> None:
        while True:
            batch = await self.queue.get()
            try:
                await self._handle(batch)
            except Exception:
                log.exception("streamer.alert_failed")
            finally:
                self.queue.task_done()

    # --- background loops --------------------------------------------------------------------

    def quote(self, symbol: str) -> dict[str, Any] | None:
        state = self.days.get(symbol)
        if state is None or state.last is None:
            return None
        ctx = self.plan.contexts.get(symbol) or self.plan.universe.get(symbol)
        prev = ctx.prev_close if ctx is not None else None
        return {
            "symbol": symbol,
            "last": state.last,
            "prev_close": prev,
            "change_pct": None if not prev else round((state.last / prev - 1) * 100, 2),
            "open": state.open,
            "high": state.high,
            "low": state.low,
            "volume": round(state.volume / self.volume_share),
            "partial_volume": self.feed.partial_volume,
            "at": state.last_ts.isoformat() if state.last_ts else None,
            "source": "stream",
        }

    def checked_quote(self, snap: Snapshot, now: datetime) -> dict[str, Any] | None:
        """A price from the pre-market check or the sweep, for a scanned stock that isn't
        streamed: its last trade this session (none yet: no quote)."""
        if snap.symbol in self.plan.contexts or snap.last is None or snap.ts is None:
            return None
        if session_date(snap.ts) != session_date(now):
            return None
        ctx = self.plan.universe.get(snap.symbol)
        prev = (ctx.prev_close if ctx is not None else None) or snap.prev_close
        return {
            "symbol": snap.symbol,
            "last": snap.last,
            "prev_close": prev,
            "change_pct": None if not prev else round((snap.last / prev - 1) * 100, 2),
            "open": snap.open,
            "high": snap.high,
            "low": snap.low,
            "volume": round(snap.volume / self.volume_share),
            "partial_volume": self.feed.partial_volume,
            "at": snap.ts.isoformat(),
            "source": "check",
        }

    async def publish_quotes(self) -> int:
        symbols, self.dirty = sorted(self.dirty), set()
        return await self._store_quotes([q for s in symbols if (q := self.quote(s)) is not None])

    async def _store_quotes(self, quotes: list[dict[str, Any]]) -> int:
        if not quotes:
            return 0
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.hset(QUOTES_KEY, mapping={q["symbol"]: json.dumps(q) for q in quotes})
            pipe.expire(QUOTES_KEY, 86400)
            pipe.publish(LIVE_CHANNEL, json.dumps({"type": "quotes", "data": quotes}))
            await pipe.execute()
        return len(quotes)

    async def flush_bars(self) -> int:
        bars, self.pending_bars = self.pending_bars, []
        if not bars:
            return 0
        ids = {c.symbol: c.ticker_id for c in self.plan.contexts.values()}
        async with self._sessions() as session:
            saved = await save_bars(session, bars, ids, self.feed.name)
        self.stats["bars"] += saved
        return saved

    async def _every(self, seconds: float, work: Callable[[], Awaitable[Any]], name: str) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await work()
            except Exception:
                log.exception("streamer.loop_failed", loop=name)

    async def _refresh_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._refresh_wanted.wait(), REFRESH_SECONDS)
            self._refresh_wanted.clear()
            try:
                await self.refresh()
            except Exception:
                log.exception("streamer.refresh_failed")

    async def _listen_for_refresh(self) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(REFRESH_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    self._refresh_wanted.set()
        finally:
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    # --- running -----------------------------------------------------------------------------

    async def _consume(self) -> None:
        async for trade in self.feed.trades():
            await self.on_trade(trade)

    async def run(self, stop: asyncio.Event) -> None:
        await self.refresh()
        loops = [
            asyncio.create_task(self._alerts_loop()),
            asyncio.create_task(self._every(QUOTE_INTERVAL_SECONDS, self.publish_quotes, "quotes")),
            asyncio.create_task(self._every(BAR_FLUSH_SECONDS, self.flush_bars, "bars")),
            asyncio.create_task(self._refresh_loop()),
            asyncio.create_task(self._listen_for_refresh()),
        ]
        consume = asyncio.create_task(self._consume())
        stopping = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({consume, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if consume.done() and not consume.cancelled() and consume.exception() is None:
                await self.finish()
            elif consume.done() and (exc := consume.exception()) is not None:
                raise exc
        finally:
            for task in [*loops, consume, stopping]:
                task.cancel()
            await asyncio.gather(*loops, consume, stopping, return_exceptions=True)
            if self._scan_task is not None:
                self._scan_task.cancel()

    async def finish(self) -> None:
        """The feed ended (a replay): record the last bars, deliver what's pending and, when
        configured, close the day."""
        for state in self.days.values():
            if (bar := state.flush()) is not None:
                self.pending_bars.append(bar)
        await self.flush_bars()
        if self._scan_task is not None:
            await asyncio.gather(self._scan_task, return_exceptions=True)
        await self.queue.join()
        await self.publish_quotes()
        log.info("streamer.feed_ended", **self.stats)
        if isinstance(self.feed, ReplayStream) and self.config.replay_close:
            await self.close_day(self.feed)

    async def close_day(self, replay: ReplayStream) -> dict[str, Any]:
        """Store the replayed session's daily bars (where missing), then run the EOD analytics
        and alerts for it: the close confirms or rejects the provisional breakouts."""
        bars = daily_bars(replay.bars)
        if not bars:
            return {}
        day = next(iter(bars.values())).date
        async with self._sessions() as session, job_lock(self.redis, INGEST_LOCK):
            found = await session.execute(
                select(Ticker.symbol, Ticker.id).where(Ticker.symbol.in_(list(bars)))
            )
            ids = {str(symbol): int(tid) for symbol, tid in found.all()}
            stored = set(
                await session.scalars(
                    select(DailyBar.ticker_id).where(
                        DailyBar.date == day, DailyBar.ticker_id.in_(list(ids.values()))
                    )
                )
            )
            missing = {
                ids[s]: PriceHistory(s, [bar])
                for s, bar in bars.items()
                if s in ids and ids[s] not in stored
            }
            if missing:
                await upsert_bars(session, missing, "replay")
                await session.commit()
            settings = await store.load(session)
            analytics: dict[str, Any] = {}
            await run_analytics(session, settings, through=day, stats=analytics)
            alerts = await eod_alerts(session, settings, day, sender=self.sender, route=self.route)
        log.info("streamer.replay_closed", session=day, daily_bars=len(missing), alerts=alerts)
        return {"session": day.isoformat(), "daily_bars": len(missing), "alerts": alerts}

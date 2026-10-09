"""The streamer end to end on a replayed session (Phase 6 acceptance): a near-pivot setup breaks
out intraday; the in-app alert and the email go out within 2 seconds of the triggering print;
at the replayed close the breakout is confirmed (or, when the move fades, rejected)."""

import asyncio
import json
import time
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.email import Message
from app.alerts.engine import LIVE_CHANNEL, EmailRoute
from app.core.calendar import MARKET_TZ
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.intraday.live import EVENTS_KEY, QUOTES_KEY
from app.intraday.plan import WatchPlan
from app.intraday.store import prev_closes
from app.intraday.watcher import Watcher
from app.models import Alert, IntradayBar, Signal, User, Watchlist, WatchlistItem
from app.providers.base import Snapshot
from app.providers.replay import ReplayStream
from app.scanner.eod_scan import run_analytics
from app.scanner.intraday_scan import WatchContext
from app.scanner.live_scans import Candidate
from app.settings.schema import AppSettings
from tests.fakes import FakeEmailSender
from tests.replay_market import SESSION, recording
from tests.test_detection_pipeline import DAYS
from tests.test_patterns import VCP
from tests.test_setups_pipeline import seed

SETTINGS = AppSettings()
LIMIT = timedelta(seconds=2)


class TimedSender(FakeEmailSender):
    def __init__(self) -> None:
        super().__init__()
        self.at: list[datetime] = []

    async def send(self, message: Message) -> None:
        await super().send(message)
        self.at.append(datetime.now(UTC))


async def market_before_the_breakout(db: AsyncSession, user: User) -> int:
    spot = await seed(db, VCP)
    await run_analytics(db, SETTINGS, through=DAYS[SESSION - 1])
    watchlist = Watchlist(user_id=user.id, name="Leaders")
    db.add(watchlist)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=watchlist.id, ticker_id=spot))
    await db.commit()
    return spot


async def replay(db: AsyncSession, *, fade: bool) -> tuple[Watcher, TimedSender, list[Any]]:
    bars = await recording(db, fade=fade)
    closes = await prev_closes(db, DAYS[SESSION], sorted({b.symbol for b in bars}))
    feed = ReplayStream(bars, speed=0, prev_closes=closes)
    sender = TimedSender()
    config = get_settings().model_copy(
        update={"replay_close": True, "public_url": "http://localhost:3000"}
    )
    watcher = Watcher(
        feed,
        sessionmaker=get_sessionmaker(),
        redis=get_redis(),
        config=config,
        sender=sender,
        route=EmailRoute(True),
    )
    pushed: list[tuple[datetime, dict[str, Any]]] = []
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(LIVE_CHANNEL)

    async def collect() -> None:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                pushed.append((datetime.now(UTC), json.loads(message["data"])))

    collector = asyncio.create_task(collect())
    started = time.perf_counter()
    await watcher.run(asyncio.Event())
    await asyncio.sleep(0.2)
    collector.cancel()
    await pubsub.aclose()  # type: ignore[no-untyped-call]
    watcher.stats["seconds"] = round(time.perf_counter() - started)
    return watcher, sender, pushed


@pytest.mark.integration
async def test_a_replayed_breakout_alerts_within_two_seconds_and_the_close_confirms_it(
    db: AsyncSession, user: User
) -> None:
    spot = await market_before_the_breakout(db, user)
    watcher, sender, pushed = await replay(db, fade=False)
    assert "SPOT" in watcher.plan.symbols
    assert watcher.plan.reasons["SPOT"] == "near pivot"

    alerts = (await db.scalars(select(Alert).order_by(Alert.id))).all()
    provisional = next(a for a in alerts if a.kind == "breakout_provisional")
    assert provisional.title == "SPOT breaking out strongly (provisional)"
    # The first print above the 92.46 pivot: the 10:15 bar's high, at 10:15:30.
    at = datetime.fromisoformat(provisional.payload["at"])
    assert at == datetime(2025, 5, 1, 10, 15, 30, tzinfo=MARKET_TZ)
    assert provisional.payload["price"] == 92.65
    assert provisional.payload["volume_ratio_pct"] > 280  # 465,000 by 10:15:30 → ~2.56M
    assert provisional.delivery == {"in_app": "sent", "email": "sent"}

    received = datetime.fromisoformat(provisional.payload["received_at"])
    in_app = next(
        when for when, m in pushed if m["type"] == "alert" and m["data"]["id"] == provisional.id
    )
    email_at = sender.at[
        next(i for i, m in enumerate(sender.sent) if m.subject == provisional.title)
    ]
    assert in_app - received < LIMIT
    assert email_at - received < LIMIT
    assert any(m["type"] == "quotes" for _, m in pushed)
    assert any(
        m["type"] == "setup_event" and m["data"]["kind"] == "breakout_provisional"
        for _, m in pushed
    )

    # The close: the day's bar came from the recording and the lifecycle confirmed it.
    signals = dict(
        (
            await db.execute(
                select(Signal.type, Signal.summary).where(
                    Signal.ticker_id == spot, Signal.date == DAYS[SESSION]
                )
            )
        ).all()
    )
    assert set(signals) >= {"breakout_provisional", "breakout"}
    assert "breakout_rejected" not in signals
    db.expire_all()
    confirmed = await db.scalar(select(Alert).where(Alert.kind == "breakout"))
    assert confirmed is not None
    assert confirmed.title == "SPOT breakout confirmed at the close"
    assert confirmed.delivery["email"] == "sent"
    assert "SPOT breakout confirmed at the close" in [m.subject for m in sender.sent]

    # Every regular-session minute of the watched stock was recorded.
    minutes = await db.scalar(
        select(func.count()).select_from(IntradayBar).where(IntradayBar.ticker_id == spot)
    )
    assert minutes == 390
    raw = await cast(Awaitable[str | None], get_redis().hget(QUOTES_KEY, "SPOT"))
    quote = json.loads(raw or "{}")
    assert quote["last"] == 92.75
    kept = await cast(Awaitable[list[str]], get_redis().lrange(EVENTS_KEY, 0, -1))
    assert [json.loads(e)["kind"] for e in kept] == ["breakout_provisional"]


@pytest.mark.integration
async def test_a_breakout_that_fades_is_rejected_at_the_close(db: AsyncSession, user: User) -> None:
    spot = await market_before_the_breakout(db, user)
    _, sender, _ = await replay(db, fade=True)
    subjects = [m.subject for m in sender.sent]
    assert subjects[0] == "SPOT breaking out strongly (provisional)"
    rejected = await db.scalar(
        select(Signal).where(Signal.ticker_id == spot, Signal.type == "breakout_rejected")
    )
    assert rejected is not None
    assert rejected.summary == (
        "Breakout rejected: It traded above the pivot 92.46 during the day (provisional at "
        "92.65) but closed back below it at 92.10."
    )
    assert "SPOT breakout rejected at the close" in subjects
    assert (
        await db.scalar(
            select(func.count())
            .select_from(Signal)
            .where(Signal.ticker_id == spot, Signal.type == "breakout")
        )
        == 0
    )


class CheckFeed:
    """A feed whose only job here is the sweep's snapshots."""

    name = "alpaca"
    partial_volume = True
    volume_share = 1.0

    def __init__(self, snaps: dict[str, Snapshot]) -> None:
        self.snaps = snaps

    async def snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        return {s: self.snaps[s] for s in symbols if s in self.snaps}


@pytest.mark.integration
async def test_the_sweep_prices_every_scanned_stock_that_isnt_streamed(clean_redis: None) -> None:
    now = datetime(2026, 10, 2, 11, 0, tzinfo=MARKET_TZ)
    earlier = now - timedelta(minutes=3)
    yesterday = now - timedelta(days=1)
    feed = CheckFeed(
        {
            "NVDA": Snapshot("NVDA", earlier, 103.0, 99.0, 101.0, 104.0, 100.5, volume=25_000),
            "SPOT": Snapshot("SPOT", earlier, 95.0, 92.0),  # streamed: its prints are fresher
            "OLD": Snapshot("OLD", yesterday, 20.0, 19.0),  # no trade yet today
            "NONE": Snapshot("NONE", None, None, 10.0),
        }
    )
    watcher = Watcher(
        cast(Any, feed),
        sessionmaker=get_sessionmaker(),
        redis=get_redis(),
        config=get_settings(),
        sender=None,
        route=EmailRoute(False),
    )
    universe = {s: Candidate(s, i, s, 100.0, 1_000_000.0) for i, s in enumerate(feed.snaps)}
    watcher.plan = WatchPlan(
        contexts={"SPOT": WatchContext("SPOT", 1, "Spotify", 92.0, 1_000_000.0)},
        universe=universe,
    )
    await watcher._scan("sweep", now)
    stored = await cast(Awaitable[dict[str, str]], get_redis().hgetall(QUOTES_KEY))
    assert list(stored) == ["NVDA"]
    share = SETTINGS.partial_feed_volume_share_pct / 100
    assert json.loads(stored["NVDA"]) == {
        "symbol": "NVDA",
        "last": 103.0,
        "prev_close": 100.0,  # our stored close, as for streamed quotes
        "change_pct": 3.0,
        "open": 101.0,
        "high": 104.0,
        "low": 100.5,
        "volume": round(25_000 / share),
        "partial_volume": True,
        "at": earlier.isoformat(),
        "source": "check",
    }

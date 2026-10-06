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
from app.intraday.store import prev_closes
from app.intraday.watcher import QUOTES_KEY, Watcher
from app.models import Alert, IntradayBar, Signal, User, Watchlist, WatchlistItem
from app.providers.replay import ReplayStream
from app.scanner.eod_scan import run_analytics
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

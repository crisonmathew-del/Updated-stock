"""The EOD alerts stage (close confirmation, signals → alerts, holdings' sell rules), email
delivery and digests.

Position used throughout: entry 100.00, initial stop 92.00 (8.00 of risk per share).
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.delivery import due_period, send_alert_emails, send_due_digests
from app.alerts.engine import AlertDraft, EmailRoute, raise_alerts
from app.alerts.eod import (
    HoldingDay,
    confirm_provisional,
    holding_drafts,
    sell_warnings,
    session_alerts,
    signal_drafts,
)
from app.core.calendar import MARKET_TZ, previous_session
from app.core.redis import get_redis
from app.data.bars import upsert_bars
from app.data.universe import plan_universe, sync_tickers
from app.intraday.store import ticker_ids
from app.models import Alert, Holding, IndicatorDaily, Signal, User
from app.providers.base import Bar, PriceHistory
from app.settings.schema import AppSettings
from tests.fakes import FakeEmailSender
from tests.test_universe import listed

DAY = date(2026, 10, 2)  # a Friday
SETTINGS = AppSettings()
ON = EmailRoute(True)
CLOSE_TIME = datetime(2026, 10, 2, 16, 30, tzinfo=MARKET_TZ)


def position(close: float, **changes: object) -> HoldingDay:
    base: dict[str, object] = {
        "symbol": "SPOT",
        "entry": 100.0,
        "stop": 92.0,
        "initial_stop": 92.0,
        "close": close,
    }
    base.update(changes)
    return HoldingDay(**base)  # type: ignore[arg-type]


def rules(h: HoldingDay) -> list[tuple[str, str, str]]:
    return [(w.rule, w.priority, w.key) for w in sell_warnings(h, SETTINGS, DAY)]


def test_stop_and_moving_average_breaks() -> None:
    # (91.50 - 100) / 8 = -1.06R; -8.5%.
    [stop] = sell_warnings(position(91.5), SETTINGS, DAY)
    assert (stop.rule, stop.priority, stop.key) == ("stop", "high", "92.00")
    assert stop.body == (
        "SPOT closed at 91.50 (-1.1R, -8.5% from your 100.00 entry), at or below your stop "
        "92.00. Your plan says sell."
    )
    # Crossed below the 50-day today (above it yesterday): high. (98.40 - 100) / 8 = -0.2R.
    crossing = position(98.4, sma50=99.0, ema21=101.0, prev_close=101.0, prev_sma50=98.5)
    assert rules(crossing) == [("below_50", "high", "2026-10-02")]
    assert "below the 50-day SMA 99.00" in sell_warnings(crossing, SETTINGS, DAY)[0].body
    # Already below it yesterday: no new warning.
    assert rules(position(98.4, sma50=99.0, prev_close=98.0, prev_sma50=99.1)) == []
    # Above the 50-day, crossed the 21-day EMA: normal.
    ema = position(103.0, sma50=97.0, ema21=104.0, prev_close=105.0, prev_ema21=103.5)
    assert rules(ema) == [("below_21", "normal", "2026-10-02")]


def test_breakeven_profit_zone_and_earnings() -> None:
    # +12% (1.5R): past the 10% breakeven trigger, short of the 20% profit zone.
    assert rules(position(112.0)) == [("breakeven", "normal", "")]
    # Once the stop is at the entry there's nothing to raise.
    assert rules(position(112.0, stop=100.0)) == []
    # +21% (2.6R): breakeven and the 20-25% profit-taking zone.
    assert rules(position(121.0)) == [("breakeven", "normal", ""), ("profit_zone", "normal", "")]
    zone = sell_warnings(position(121.0), SETTINGS, DAY)[1]
    assert zone.body == (
        "SPOT closed at 121.00 (+2.6R, +21.0% from your 100.00 entry): inside the 20-25% zone "
        "where the plan takes partial profits."
    )
    soon = position(101.0, next_earnings=(date(2026, 10, 7), 3))
    [earnings] = sell_warnings(soon, SETTINGS, DAY)
    assert (earnings.rule, earnings.key) == ("earnings", "2026-10-07")
    assert earnings.body.startswith("Expected Wed Oct 7 (3 sessions away, estimated).")
    assert rules(position(101.0, next_earnings=(date(2026, 10, 20), 12))) == []


def test_digests_are_due_after_their_time() -> None:
    friday = datetime(2026, 10, 2, 17, 29, tzinfo=MARKET_TZ)
    assert due_period("daily", SETTINGS, friday) is None
    assert due_period("daily", SETTINGS, friday.replace(minute=30)) == "2026-10-02"
    assert (
        due_period("daily", AppSettings(daily_digest_enabled=False), friday.replace(hour=20))
        is None
    )
    saturday = datetime(2026, 10, 3, 18, 0, tzinfo=MARKET_TZ)
    assert due_period("daily", SETTINGS, saturday) is None  # not a trading day
    assert due_period("weekly", SETTINGS, saturday) is None
    sunday = datetime(2026, 10, 4, 18, 0, tzinfo=MARKET_TZ)
    assert due_period("weekly", SETTINGS, sunday) == "2026-W40"
    assert due_period("weekly", SETTINGS, sunday.replace(hour=17)) is None


async def _stocks(db: AsyncSession) -> dict[str, int]:
    await sync_tickers(db, plan_universe(listed("SPOT", "AAPL")), DAY)
    return await ticker_ids(db, ["SPOT", "AAPL"])


def _signal(tid: int, kind: str, **values: object) -> Signal:
    base: dict[str, object] = {
        "date": DAY,
        "type": kind,
        "ticker_id": tid,
        "summary": f"{kind} summary",
        "price": 92.80,
        "pivot": 92.46,
        "entry": 92.56,
        "stop": 88.71,
        "score": 84.2,
        "grade": "A",
        "context": {"trade_plan": {"shares": 259, "buy_zone": [92.46, 97.08]}},
    }
    base.update(values)
    return Signal(**base)


@pytest.mark.integration
async def test_the_close_confirms_or_rejects_provisional_breakouts(
    db: AsyncSession, user: User
) -> None:
    ids = await _stocks(db)
    db.add_all(
        [
            _signal(ids["SPOT"], "breakout_provisional"),
            _signal(ids["SPOT"], "breakout", summary="Breakout confirmed: closed 1.2% above."),
            _signal(ids["AAPL"], "breakout_provisional", price=93.10),
        ]
    )
    await db.commit()
    # AAPL closed back below its pivot.
    history = PriceHistory("AAPL", [Bar(DAY, 92.0, 93.4, 91.6, 92.10, 5_000_000)])
    await upsert_bars(db, {ids["AAPL"]: history}, "test")
    await db.commit()

    assert await confirm_provisional(db, DAY) == {"confirmed": 1, "rejected": 1, "waiting": 0}
    rejected = await db.scalar(select(Signal).where(Signal.type == "breakout_rejected"))
    assert rejected is not None
    assert rejected.summary == (
        "Breakout rejected: It traded above the pivot 92.46 during the day (provisional at "
        "93.10) but closed back below it at 92.10."
    )
    assert rejected.price == 92.10
    # Settling again changes nothing.
    assert await confirm_provisional(db, DAY) == {"confirmed": 1, "rejected": 1, "waiting": 0}

    drafts = await signal_drafts(db, DAY)
    assert [(d.symbol, d.kind, d.priority, d.title) for d in drafts] == [
        ("SPOT", "breakout", "high", "SPOT breakout confirmed at the close"),
        ("AAPL", "breakout_rejected", "high", "AAPL breakout rejected at the close"),
    ]
    assert drafts[0].payload["buy_zone_top"] == 97.08
    assert drafts[0].payload["shares"] == 259
    await raise_alerts(db, get_redis(), drafts, SETTINGS, CLOSE_TIME, ON)
    # Signals that alerted never alert again (a re-run of the session).
    assert await signal_drafts(db, DAY) == []


@pytest.mark.integration
async def test_holdings_warn_once(db: AsyncSession, user: User) -> None:
    ids = await _stocks(db)
    before = previous_session(DAY)
    await upsert_bars(
        db,
        {
            ids["SPOT"]: PriceHistory(
                "SPOT",
                [Bar(before, 100, 102, 99, 101.0, 1_000), Bar(DAY, 100, 100, 97, 98.4, 1_000)],
            )
        },
        "test",
    )
    db.add_all(
        [
            IndicatorDaily(ticker_id=ids["SPOT"], date=before, ema21=100.5, sma50=98.5),
            IndicatorDaily(ticker_id=ids["SPOT"], date=DAY, ema21=100.4, sma50=99.0),
            Holding(
                user_id=user.id,
                ticker_id=ids["SPOT"],
                opened_on=date(2026, 9, 1),
                entry_price=100.0,
                shares=100,
                initial_stop=92.0,
                stop=92.0,
            ),
        ]
    )
    await db.commit()
    drafts = await holding_drafts(db, DAY, SETTINGS)
    assert [(d.kind, d.user_id, d.dedupe_key) for d in drafts] == [
        ("holding_below_50", user.id, "holding:1:below_50:2026-10-02")
    ]
    assert drafts[0].payload["r"] == pytest.approx(-0.2)
    await raise_alerts(db, get_redis(), drafts, SETTINGS, CLOSE_TIME, ON)
    assert await holding_drafts(db, DAY, SETTINGS) == []


@pytest.mark.integration
async def test_queued_alerts_are_emailed_and_the_outcome_recorded(
    db: AsyncSession, user: User
) -> None:
    ids = await _stocks(db)
    db.add(_signal(ids["SPOT"], "breakout"))
    await db.commit()
    stats, queued = await session_alerts(db, get_redis(), SETTINGS, DAY, CLOSE_TIME, ON)
    assert (stats["alerts"], stats["emails_queued"]) == (1, 1)

    sender = FakeEmailSender()
    counts = await send_alert_emails(
        db, sender, queued, public_url="http://localhost:3000", email_to=None
    )
    assert dict(counts) == {"sent": 1}
    [message] = sender.sent
    assert (message.to, message.subject) == ("owner@example.com", "SPOT breakout confirmed")
    assert "http://localhost:3000/stocks/SPOT" in message.text
    alert = await db.scalar(select(Alert))
    assert alert is not None
    await db.refresh(alert)
    assert alert.delivery == {"in_app": "sent", "email": "sent"}
    # Already sent: not again.
    assert dict(await send_alert_emails(db, sender, queued, public_url="x", email_to=None)) == {}

    db.add(_signal(ids["AAPL"], "breakout", date=DAY))
    await db.commit()
    _, queued = await session_alerts(db, get_redis(), SETTINGS, DAY, CLOSE_TIME, ON)
    failing = FakeEmailSender(fail="SMTP mail.example.com:587 failed: 535 bad credentials")
    counts = await send_alert_emails(db, failing, queued, public_url="x", email_to="me@example.com")
    assert dict(counts) == {"failed": 1}
    failed = await db.get(Alert, queued[0])
    assert failed is not None
    await db.refresh(failed)
    assert (
        failed.delivery["email"] == "failed: SMTP mail.example.com:587 failed: 535 bad credentials"
    )


@pytest.mark.integration
async def test_the_daily_digest_collects_pending_alerts_once(db: AsyncSession, user: User) -> None:
    redis = get_redis()
    drafts = [
        AlertDraft(
            "near_pivot",
            "normal",
            "AAPL is near its pivot",
            "1.2% below.",
            DAY,
            "near:1",
            "AAPL",
            grade="A",
        ),
        AlertDraft(
            "breakout",
            "high",
            "SPOT breakout confirmed",
            "Closed above.",
            DAY,
            "b:1",
            "SPOT",
            grade="A",
        ),
    ]
    created = await raise_alerts(db, redis, drafts, SETTINGS, CLOSE_TIME, ON)
    created[1].delivery = {**created[1].delivery, "email": "sent"}
    await db.commit()

    sender = FakeEmailSender()
    at = datetime(2026, 10, 2, 21, 35, tzinfo=UTC)  # 17:35 ET
    out = await send_due_digests(
        db, redis, sender, SETTINGS, at, public_url="http://localhost:3000", email_to=None
    )
    assert out == {"daily": {"kind": "daily", "sent": 1}}
    [message] = sender.sent
    assert message.subject == "Daily digest · Friday, October 2"
    assert "- AAPL is near its pivot: 1.2% below." in message.text
    assert "1 more alert went out by email as they happened." in message.text
    pending = await db.get(Alert, created[0].id)
    assert pending is not None
    await db.refresh(pending)
    assert pending.digested_at == at
    assert pending.delivery["email"] == "daily digest: sent"
    # The next tick sends nothing more.
    assert (
        await send_due_digests(db, redis, sender, SETTINGS, at, public_url="x", email_to=None) == {}
    )
    assert len(sender.sent) == 1

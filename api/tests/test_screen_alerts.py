"""A saved screen promoted to an alert: the server applies the screen's filters exactly as the
web does, and alerts for stocks that newly match after each close (the first run is the
baseline)."""

from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import screens
from app.core.redis import get_redis
from app.models import AlertRule, SavedScreen, User
from app.scanner.screener_rows import matches

ROW = {"symbol": "SPOT", "rs_rating": 94, "tt_pass": True, "pattern": "vcp", "close": 92.75}


def test_filters_match_like_the_web() -> None:
    assert matches(ROW, [{"field": "tt_pass", "op": "is", "value": True}])
    assert not matches(ROW, [{"field": "tt_pass", "op": "is", "value": False}])
    assert matches(ROW, [{"field": "pattern", "op": "in", "values": ["vcp", "flat_base"]}])
    assert matches(ROW, [{"field": "pattern", "op": "in", "values": []}])  # nothing chosen: any
    assert not matches(ROW, [{"field": "pattern", "op": "in", "values": ["cup"]}])
    assert matches(ROW, [{"field": "rs_rating", "op": "between", "min": 90, "max": None}])
    assert not matches(ROW, [{"field": "rs_rating", "op": "between", "min": None, "max": 90}])
    assert matches(ROW, [{"field": "rs_rating", "op": "between", "min": None, "max": None}])
    # A missing number never passes a range.
    assert not matches(ROW, [{"field": "score", "op": "between", "min": 0, "max": None}])


def snapshot(*rows: dict[str, Any]) -> dict[str, Any]:
    fields = ["symbol", "rs_rating", "close", "change_pct", "grade", "pattern", "setup_state"]
    return {"fields": fields, "rows": [[r.get(f) for f in fields] for r in rows]}


@pytest.mark.integration
async def test_new_matches_alert_after_the_baseline(
    db: AsyncSession, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = SavedScreen(
        user_id=user.id,
        name="RS 90+",
        filters=[{"field": "rs_rating", "op": "between", "min": 90, "max": None}],
    )
    db.add(screen)
    await db.flush()
    db.add(
        AlertRule(
            user_id=user.id,
            name="New RS leaders",
            scope="screen",
            screen_id=screen.id,
            condition="new_match",
            channels=["in_app"],
            priority="normal",
        )
    )
    await db.commit()
    days: dict[date, dict[str, Any]] = {
        date(2026, 9, 30): snapshot({"symbol": "AAPL", "rs_rating": 91}),
        date(2026, 10, 1): snapshot(
            {"symbol": "AAPL", "rs_rating": 92},
            {
                "symbol": "SPOT",
                "rs_rating": 94,
                "close": 92.75,
                "change_pct": 1.37,
                "grade": "A",
                "pattern": "vcp",
                "setup_state": "near_pivot",
            },
            {"symbol": "TSM", "rs_rating": 60},
        ),
    }

    async def fake_snapshot(_: AsyncSession, day: date) -> dict[str, Any]:
        return days[day]

    monkeypatch.setattr(screens, "build_snapshot", fake_snapshot)
    redis = get_redis()
    assert await screens.screen_drafts(db, redis, date(2026, 9, 30)) == []  # the baseline
    drafts = await screens.screen_drafts(db, redis, date(2026, 10, 1))
    assert [(d.symbol, d.title, d.body, d.channels) for d in drafts] == [
        (
            "SPOT",
            "SPOT entered your screen “RS 90+”",
            "Close 92.75 (+1.4%), RS 94, setup grade A, vcp near pivot.",
            ("in_app",),
        )
    ]
    # Re-running the same session adds nothing.
    assert await screens.screen_drafts(db, redis, date(2026, 10, 1)) == []

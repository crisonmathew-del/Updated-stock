"""Saved screens promoted to alerts (spec §7.3): after the close, each enabled screen rule runs
its screen on the session's snapshot and alerts for every stock that newly matches.

"Newly" is against the previous run of that rule (kept in Redis): a rule's first run only
records what matches (alerting on the whole list at once would be noise). Many new matches
in one session become one alert per stock up to MAX_PER_RULE, then one summary alert.
"""

import json
from datetime import date
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.engine import AlertDraft
from app.core.logging import get_logger
from app.intraday.store import ticker_ids
from app.models import AlertRule, SavedScreen
from app.scanner.screener_rows import build_snapshot, matches, row_dicts

log = get_logger(__name__)

MAX_PER_RULE = 20
STATE_TTL_SECONDS = 30 * 86400


def state_key(rule_id: int) -> str:
    return f"screen_rule:{rule_id}"


def describe(row: dict[str, Any]) -> str:
    parts = []
    if row.get("close") is not None:
        change = row.get("change_pct")
        parts.append(
            f"Close {row['close']:,.2f}" + (f" ({change:+.1f}%)" if change is not None else "")
        )
    if row.get("rs_rating") is not None:
        parts.append(f"RS {row['rs_rating']}")
    if row.get("grade"):
        parts.append(f"setup grade {row['grade']}")
    if row.get("pattern"):
        state = (row.get("setup_state") or "").replace("_", " ")
        parts.append(f"{row['pattern'].replace('_', ' ')} {state}".strip())
    return ", ".join(parts) + "."


async def screen_drafts(session: AsyncSession, redis: Redis, day: date) -> list[AlertDraft]:
    rules = (
        await session.execute(
            select(AlertRule, SavedScreen)
            .join(SavedScreen, SavedScreen.id == AlertRule.screen_id)
            .where(AlertRule.enabled, AlertRule.scope == "screen")
        )
    ).all()
    if not rules:
        return []
    rows = row_dicts(await build_snapshot(session, day))
    drafts: list[AlertDraft] = []
    for rule, screen in rules:
        matched = {r["symbol"]: r for r in rows if matches(r, screen.filters or [])}
        raw = await redis.get(state_key(rule.id))
        previous = json.loads(raw) if raw else None
        await redis.set(
            state_key(rule.id),
            json.dumps({"date": day.isoformat(), "symbols": sorted(matched)}),
            ex=STATE_TTL_SECONDS,
        )
        if previous is None or previous.get("date") == day.isoformat():
            continue  # the first run is the baseline; a re-run of the session adds nothing
        new = sorted(set(matched) - set(previous.get("symbols", [])))
        ids = await ticker_ids(session, new[:MAX_PER_RULE]) if new else {}
        channels = tuple(rule.channels or ()) or None
        for symbol in new[:MAX_PER_RULE]:
            row = matched[symbol]
            drafts.append(
                AlertDraft(
                    kind="screen_match",
                    priority=rule.priority,
                    title=f"{symbol} entered your screen “{screen.name}”",
                    body=describe(row),
                    session_date=day,
                    dedupe_key=f"screen:{rule.id}:{symbol}:{day.isoformat()}",
                    symbol=symbol,
                    ticker_id=ids.get(symbol),
                    user_id=rule.user_id,
                    payload={
                        "price": row.get("close"),
                        "change_pct": row.get("change_pct"),
                        "pivot": row.get("pivot"),
                        "grade": row.get("grade"),
                        "score": row.get("score"),
                        "screen_id": screen.id,
                    },
                    rule_id=rule.id,
                    channels=channels,
                )
            )
        if len(new) > MAX_PER_RULE:
            rest = new[MAX_PER_RULE:]
            drafts.append(
                AlertDraft(
                    kind="screen_match",
                    priority="normal",
                    title=f"{len(rest)} more stocks entered “{screen.name}”",
                    body=", ".join(rest[:50]) + ("…" if len(rest) > 50 else "") + ".",
                    session_date=day,
                    dedupe_key=f"screen:{rule.id}:more:{day.isoformat()}",
                    user_id=rule.user_id,
                    payload={"screen_id": screen.id, "symbols": rest},
                    rule_id=rule.id,
                    channels=channels,
                )
            )
        log.info("alerts.screen_rule", rule_id=rule.id, matches=len(matched), new=len(new))
    return drafts

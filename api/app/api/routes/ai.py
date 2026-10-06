"""The AI summary on the stock page (spec §6.12; see app.ai.summary).

- GET  /stocks/{symbol}/ai-summary   {enabled, model, summary}: the cached summary, if any
- POST /stocks/{symbol}/ai-summary   ?refresh=true: write one (or return today's cached one)
A summary is cached for 24 hours per stock and session; one stock is summarised at a time.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.ai.summary import (
    CACHE_SECONDS,
    SummaryError,
    anthropic_call,
    cache_key,
    enabled,
    model_name,
    write_summary,
)
from app.api.deps import DbSession, RedisClient, current_user
from app.api.routes.market import market_regime
from app.api.routes.setups import stock_setup
from app.api.routes.stocks import stock_fundamentals, stock_summary
from app.core.config import get_settings

router = APIRouter(prefix="/stocks", tags=["ai"], dependencies=[Depends(current_user)])

LOCK_SECONDS = 180
NO_NEWS = "No news source is connected, so there are no headlines."


class AiSummary(BaseModel):
    thesis: str
    catalyst: str
    risks: list[str]
    unverified: list[str]  # numbers in the text that the data doesn't contain
    model: str
    generated_at: str
    as_of: str | None  # the session the data describes
    cached: bool = False


class AiSummaryOut(BaseModel):
    enabled: bool
    model: str
    summary: AiSummary | None


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: source.get(k) for k in keys}


async def stock_facts(db: DbSession, symbol: str) -> dict[str, Any]:
    """What the summary may use: the stock page's own data, trimmed to what matters."""
    s = (await stock_summary(db, symbol)).model_dump(mode="json")
    setup = await stock_setup(db, symbol)
    fundamentals = (await stock_fundamentals(db, symbol)).model_dump(mode="json")
    regime = (await market_regime(db, days=1)).model_dump(mode="json")
    facts: dict[str, Any] = {
        "stock": _pick(
            s,
            "symbol",
            "name",
            "exchange",
            "sector",
            "industry",
            "market_cap",
            "date",
            "close",
            "change_pct",
            "volume_ratio",
            "high_52w",
            "low_52w",
            "stage_label",
            "rs_rating",
            "next_earnings",
            "sessions_to_earnings",
        ),
        "trend_template": {
            "passed": f"{s['trend_template_passed']} of 8",
            "checks": [_pick(c, "label", "passed", "detail") for c in s["checks"]],
        },
        "industry_group": s.get("group"),
        "setup": None,
        "fundamentals": None,
        "market": {"regime": regime.get("label"), "reasons": regime.get("reasons", [])[:3]},
        "headlines": [],
        "news": NO_NEWS,
    }
    if setup is not None:
        d = setup.model_dump(mode="json")
        plan = d.get("trade_plan") or {}
        facts["setup"] = {
            **_pick(
                d,
                "state_label",
                "pattern_label",
                "pivot",
                "readiness_pct",
                "score",
                "grade",
                "breakout_date",
            ),
            "score_parts": [
                _pick(c, "label", "points", "max_points", "detail") for c in d["components"]
            ],
            "red_flags": d["red_flags"],
            "trade_plan": _pick(
                plan,
                "entry",
                "stop",
                "stop_basis",
                "risk_pct",
                "risk_too_wide",
                "buy_zone",
                "target_2r",
                "target_3r",
                "profit_take",
                "breakeven_at",
            )
            if plan
            else None,
            "recent_signals": [_pick(x, "date", "type_label", "summary") for x in d["signals"][:5]],
        }
    grade = fundamentals.get("grade")
    if grade:
        facts["fundamentals"] = {
            **_pick(grade, "grade", "score", "basis", "coverage_pct"),
            "parts": [
                _pick(c, "label", "points", "max_points", "status", "detail")
                for c in grade["components"]
            ],
            "recent_quarters": [
                _pick(q, "label", "reported_date", "eps", "eps_growth_pct", "revenue_growth_pct")
                for q in fundamentals["quarters"][:4]
            ],
        }
    return facts


@router.get("/{symbol}/ai-summary")
async def get_summary(symbol: str, db: DbSession, redis: RedisClient) -> AiSummaryOut:
    config = get_settings()
    s = await stock_summary(db, symbol)
    raw = await redis.get(cache_key(s.symbol, None if s.date is None else s.date.isoformat()))
    summary = (
        None
        if raw is None
        else AiSummary.model_validate_json(raw).model_copy(update={"cached": True})
    )
    return AiSummaryOut(enabled=enabled(config), model=model_name(config), summary=summary)


@router.post("/{symbol}/ai-summary")
async def make_summary(
    symbol: str,
    db: DbSession,
    redis: RedisClient,
    refresh: Annotated[bool, Query()] = False,
) -> AiSummaryOut:
    config = get_settings()
    if not enabled(config):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI summaries are off: add ANTHROPIC_API_KEY to .env and restart the api service.",
        )
    facts = await stock_facts(db, symbol)
    stock = facts["stock"]
    key = cache_key(stock["symbol"], stock["date"])
    model = model_name(config)
    if not refresh:
        raw = await redis.get(key)
        if raw is not None:
            cached = AiSummary.model_validate_json(raw).model_copy(update={"cached": True})
            return AiSummaryOut(enabled=True, model=model, summary=cached)
    lock = f"{key}:lock"
    if not await redis.set(lock, "1", nx=True, ex=LOCK_SECONDS):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A summary of this stock is being written: wait a moment."
        )
    try:
        written = await write_summary(facts, model, anthropic_call(config))
    except SummaryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    finally:
        await redis.delete(lock)
    summary = AiSummary(**written, as_of=stock["date"])
    await redis.set(key, summary.model_dump_json(), ex=CACHE_SECONDS)
    return AiSummaryOut(enabled=True, model=model, summary=summary)

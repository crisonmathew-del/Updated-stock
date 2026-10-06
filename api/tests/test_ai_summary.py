"""The AI summary without calling the API: the numbers check, the request (structured output,
the refusal fallback), regenerating once when the reply cites numbers that aren't in the data,
refusals, and the endpoint (off without a key, cached per stock and session)."""

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.summary import (
    FALLBACK_BETA,
    SummaryError,
    known_values,
    request,
    unverified,
    write_summary,
)
from app.api.routes import ai as ai_routes
from app.core.config import get_settings
from app.scanner.eod_scan import run_analytics
from app.settings.schema import AppSettings
from tests.test_detection_pipeline import DAYS
from tests.test_patterns import VCP
from tests.test_setups_pipeline import seed

FACTS: dict[str, Any] = {
    "stock": {"symbol": "SPOT", "close": 92.75, "date": "2025-05-01", "market_cap": 25_300_000_000},
    "checks": [{"detail": "Close 92.75 vs 150-day 85.10 (+9.0%)"}],
    "setup": {"pivot": 92.46, "trade_plan": {"entry": 92.56, "stop": 88.71, "risk_pct": 4.16}},
}


def reply(brief: dict[str, Any], stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        stop_reason=stop,
        model="claude-opus-5-5",
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=json.dumps(brief)),
        ],
    )


def test_numbers_must_come_from_the_data() -> None:
    known = known_values(FACTS)
    fine = [
        "Closed at 92.8, 9% above the 85.1 150-day average on 2025-05-01.",
        "A 25.3 billion company; entry 92.56, stop 88.71 (4.2% risk), 3 contractions.",
    ]
    assert unverified(fine, known) == []
    made_up = ["A move to 120 by 2025-06-01 looks likely; 92.75 holds."]
    assert unverified(made_up, known) == ["2025-06-01", "120"]


def test_the_request_asks_for_json_and_opts_into_the_fallback() -> None:
    body = request("claude-opus-5-5", FACTS)
    assert body["fallbacks"] == "default"
    assert body["betas"] == [FALLBACK_BETA]
    assert body["output_config"]["format"]["schema"]["required"] == ["thesis", "catalyst", "risks"]
    assert "<data>" in body["messages"][0]["content"]
    assert "Use only numbers that appear in the data" in body["system"]
    other = request("claude-haiku-4-5", FACTS, avoid=["120"])
    assert "fallbacks" not in other
    assert "these numbers, which are not in the data: 120" in other["messages"][0]["content"]


async def test_a_reply_with_invented_numbers_is_written_again_once() -> None:
    calls: list[dict[str, Any]] = []
    replies = [
        reply({"thesis": "Targets 120.", "catalyst": "None.", "risks": ["Stop 88.71."]}),
        reply({"thesis": "Near the 92.46 pivot.", "catalyst": "None.", "risks": ["Stop 88.71."]}),
    ]

    async def call(body: dict[str, Any]) -> Any:
        calls.append(body)
        return replies[len(calls) - 1]

    out = await write_summary(FACTS, "claude-opus-5-5", call)
    assert len(calls) == 2
    assert "120" in calls[1]["messages"][0]["content"]
    assert (out["thesis"], out["unverified"]) == ("Near the 92.46 pivot.", [])

    async def stubborn(body: dict[str, Any]) -> Any:
        return reply({"thesis": "Targets 120.", "catalyst": "-", "risks": []})

    # Still wrong after the second try: returned, with the numbers listed for the page.
    assert (await write_summary(FACTS, "claude-opus-5-5", stubborn))["unverified"] == ["120"]


async def test_a_refusal_is_an_error_in_words() -> None:
    async def refuse(body: dict[str, Any]) -> Any:
        return reply({}, stop="refusal")

    with pytest.raises(SummaryError, match="declined") as caught:
        await write_summary(FACTS, "claude-opus-5-5", refuse)
    assert caught.value.status == 422


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_the_endpoint_is_off_without_a_key_and_caches_per_session(
    db: AsyncSession, signed_in: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(db, VCP)
    await run_analytics(db, AppSettings(), through=DAYS[332])
    off = (await signed_in.get("/api/stocks/SPOT/ai-summary")).json()
    assert off == {"enabled": False, "model": "claude-opus-5-5", "summary": None}
    refused = await signed_in.post("/api/stocks/SPOT/ai-summary")
    assert refused.status_code == 503
    assert "ANTHROPIC_API_KEY" in refused.json()["detail"]

    monkeypatch.setattr(get_settings(), "anthropic_api_key", SecretStr("sk-test"))
    sent: list[dict[str, Any]] = []

    async def call(body: dict[str, Any]) -> Any:
        sent.append(body)
        return reply(
            {
                "thesis": "SPOT is near its 92.46 pivot in a volatility contraction.",
                "catalyst": "No news source is connected.",
                "risks": ["Stop at 88.71."],
            }
        )

    monkeypatch.setattr(ai_routes, "anthropic_call", lambda config: call)
    made = (await signed_in.post("/api/stocks/SPOT/ai-summary")).json()
    assert made["summary"]["thesis"].startswith("SPOT is near")
    assert made["summary"]["as_of"] == DAYS[332].isoformat()
    assert made["summary"]["cached"] is False
    facts = sent[0]["messages"][0]["content"]
    assert '"headlines": []' in facts
    assert '"pattern_label": "Volatility contraction (VCP)"' in facts
    again = (await signed_in.post("/api/stocks/SPOT/ai-summary")).json()
    assert (again["summary"]["cached"], len(sent)) == (True, 1)
    shown = (await signed_in.get("/api/stocks/SPOT/ai-summary")).json()
    assert shown["enabled"] is True
    assert shown["summary"]["thesis"] == made["summary"]["thesis"]
    fresh = (await signed_in.post("/api/stocks/SPOT/ai-summary", params={"refresh": True})).json()
    assert (fresh["summary"]["cached"], len(sent)) == (False, 2)

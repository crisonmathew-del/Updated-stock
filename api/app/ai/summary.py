"""The optional AI summary on the stock page (spec §6.12): the stock's structured data goes to the
Claude API, which writes a short thesis (why it does or doesn't qualify), the catalyst ("N", only
from headlines passed in) and the main risks.

Numbers: the instructions allow only figures that appear in the data, and `unverified` checks
the reply: every number in it must match one in the data (rounding allowed). A reply that cites
others is regenerated once with those numbers named; if it still does, the summary is returned
with them listed so the page can say so. The summary is labelled "AI summary" and cached for 24
hours per stock and session. Off until ANTHROPIC_API_KEY is set; the model is ANTHROPIC_MODEL
(default claude-opus-5-5). The key is only ever read here, server-side.
"""

import json
import math
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from app.core.config import Settings

DEFAULT_MODEL = "claude-opus-5-5"
# Models that take the server-side refusal fallback ("default" picks the substitute by category).
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CACHE_SECONDS = 24 * 3600
MAX_TOKENS = 16000
TIMEOUT_SECONDS = 120.0
# Indicator names and small counts read as words, not figures ("50-day", "4 contractions").
ALWAYS_OK = frozenset({10.0, 21.0, 50.0, 150.0, 200.0, 52.0, *map(float, range(13))})

SYSTEM = """You write a short brief about one US stock for a trader who follows a growth-stock \
method (CAN SLIM, Weinstein stages, Minervini's Trend Template and volatility contraction \
patterns). The data comes from the trader's own screening system, as JSON inside <data> tags.

Write three parts:
- thesis: three to five sentences on why the stock does or doesn't qualify now, by the \
system's own checks: trend and stage, relative strength, the base or setup and its grade, \
fundamentals, the industry group and the market.
- catalyst: the "N" in CAN SLIM (a new product, management, an earnings surprise, new highs), \
taken only from the headlines in the data. If there are no headlines, say that no news source \
is connected, then name anything in the data itself that is new (such as a recent results \
release or new highs), if there is something.
- risks: two to four short points: red flags, failed checks, the distance to the stop, \
earnings timing, the market regime.

Rules:
- Use only numbers that appear in the data, written as they appear there; rounding to fewer \
decimals is fine. Don't calculate new figures, estimate, or bring in numbers from elsewhere. \
Dates count as numbers.
- No price targets, and don't tell the trader to buy or sell: describe what the system's rules \
show.
- When something isn't in the data, say it isn't available instead of guessing.
- Plain, specific English, no headings or markdown."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string"},
        "catalyst": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["thesis", "catalyst", "risks"],
    "additionalProperties": False,
}


class Brief(BaseModel):
    thesis: str
    catalyst: str
    risks: list[str]


class SummaryError(Exception):
    """Something the page should show in words (with the HTTP status to use)."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def model_name(config: Settings) -> str:
    return (config.anthropic_model or "").strip() or DEFAULT_MODEL


def enabled(config: Settings) -> bool:
    key = config.anthropic_api_key
    return key is not None and bool(key.get_secret_value().strip())


# --- The numbers check ------------------------------------------------------------------------

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _leaves(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _leaves(v)
    else:
        yield value


@dataclass(frozen=True)
class Known:
    dates: frozenset[str]
    numbers: tuple[float, ...]


def known_values(facts: dict[str, Any]) -> Known:
    """Every date and number in the data, including those inside text (check details)."""
    dates: set[str] = set()
    numbers: set[float] = set()
    for leaf in _leaves(facts):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, int | float):
            if math.isfinite(leaf):
                numbers.add(abs(float(leaf)))
            continue
        text = str(leaf)
        dates.update(DATE.findall(text))
        for token in NUMBER.findall(DATE.sub(" ", text)):
            numbers.add(float(token.replace(",", "")))
    return Known(frozenset(dates), tuple(sorted(numbers)))


def _matches(token: str, known: Known) -> bool:
    value = float(token.replace(",", ""))
    if value in ALWAYS_OK:
        return True
    decimals = len(token.split(".")[1]) if "." in token else 0
    for scale in (1.0, 1e3, 1e6, 1e9, 1e12):  # "25.3 billion" for 25,300,000,000
        tolerance = 0.5 * 10**-decimals * scale + 1e-9
        target = value * scale
        if any(abs(target - n) <= tolerance for n in known.numbers):
            return True
    return False


def unverified(texts: Iterable[str], known: Known) -> list[str]:
    """Numbers and dates in `texts` that don't appear in the data."""
    out: list[str] = []
    for text in texts:
        for day in DATE.findall(text):
            if day not in known.dates and day not in out:
                out.append(day)
        for token in NUMBER.findall(DATE.sub(" ", text)):
            if not _matches(token, known) and token not in out:
                out.append(token)
    return out


# --- The request ------------------------------------------------------------------------------

Call = Callable[[dict[str, Any]], Awaitable[Any]]


def request(model: str, facts: dict[str, Any], avoid: list[str] | None = None) -> dict[str, Any]:
    """The Messages API request body (as keyword arguments)."""
    prompt = f"<data>\n{json.dumps(facts, indent=1, sort_keys=True, default=str)}\n</data>\n\n"
    prompt += "Write the brief."
    if avoid:
        prompt += (
            " An earlier draft used these numbers, which are not in the data: "
            + ", ".join(avoid)
            + ". Leave them out."
        )
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    }
    if model in FALLBACK_MODELS:
        body["betas"] = [FALLBACK_BETA]
        body["fallbacks"] = "default"
    return body


def anthropic_call(config: Settings) -> Call:
    """Send a request with the configured key (the beta endpoint carries the fallback)."""

    async def call(body: dict[str, Any]) -> Any:
        assert config.anthropic_api_key is not None
        async with anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key.get_secret_value(),
            timeout=TIMEOUT_SECONDS,
        ) as client:
            return await client.beta.messages.create(**body)

    return call


def _brief(response: Any) -> Brief:
    if response.stop_reason == "refusal":
        raise SummaryError("Claude declined to summarise this stock.", 422)
    if response.stop_reason == "max_tokens":
        raise SummaryError("The summary ran past its length limit. Try again.", 502)
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return Brief.model_validate_json(text)
    except ValidationError as exc:
        raise SummaryError("The summary came back in an unexpected shape. Try again.") from exc


async def write_summary(facts: dict[str, Any], model: str, call: Call) -> dict[str, Any]:
    """Generate, check the numbers, regenerate once if needed. Raises SummaryError."""
    known = known_values(facts)
    try:
        response = await call(request(model, facts))
        brief = _brief(response)
        wrong = unverified([brief.thesis, brief.catalyst, *brief.risks], known)
        if wrong:
            response = await call(request(model, facts, avoid=wrong))
            brief = _brief(response)
            wrong = unverified([brief.thesis, brief.catalyst, *brief.risks], known)
    except anthropic.AuthenticationError as exc:
        raise SummaryError("The Anthropic API rejected the key: check ANTHROPIC_API_KEY.") from exc
    except anthropic.RateLimitError as exc:
        raise SummaryError(
            "The Anthropic API is rate limiting: try again in a minute.", 503
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise SummaryError("Can't reach the Anthropic API: check the network.") from exc
    except anthropic.APIStatusError as exc:
        raise SummaryError(f"The Anthropic API returned an error ({exc.status_code}).") from exc
    return {
        **brief.model_dump(),
        "unverified": wrong,
        "model": str(getattr(response, "model", model)),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def cache_key(symbol: str, as_of: str | None) -> str:
    return f"ai:summary:{symbol.upper()}:{as_of or 'none'}"

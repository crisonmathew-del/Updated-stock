"""SEC EDGAR: ticker → CIK mapping, SIC classification and cover-page share counts.

Free, no key. SEC requires a descriptive User-Agent with contact details (SEC_USER_AGENT) and
allows at most 10 requests per second; we stay at 8.
https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

from collections import defaultdict
from datetime import date
from typing import Any, ClassVar

import httpx

from app.core.config import get_settings
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.providers import http
from app.providers.base import (
    CompanyIdentifier,
    CompanyReference,
    FundamentalsProvider,
    ProviderError,
    ProviderNotConfiguredError,
    SharesObservation,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
REQUESTS_PER_SECOND = 8


def pad_cik(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def sec_to_symbol(ticker: str) -> str:
    """SEC writes share classes with a dash (BRK-B); we use the exchange dot form (BRK.B)."""
    return ticker.upper().replace("-", ".")


def parse_company_tickers(payload: dict[str, Any]) -> list[CompanyIdentifier]:
    fields = payload["fields"]
    index = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    return [
        CompanyIdentifier(
            symbol=sec_to_symbol(row[index["ticker"]]),
            cik=pad_cik(row[index["cik"]]),
            name=row[index["name"]],
            exchange=row[index["exchange"]],
        )
        for row in payload["data"]
        if row[index["ticker"]]
    ]


def parse_submissions(payload: dict[str, Any]) -> CompanyReference:
    sic = str(payload.get("sic") or "").strip()
    return CompanyReference(
        cik=pad_cik(payload["cik"]),
        name=payload.get("name") or "",
        sic_code=sic or None,
        sic_description=(payload.get("sicDescription") or None) if sic else None,
    )


def parse_shares_outstanding(payload: dict[str, Any]) -> list[SharesObservation]:
    """Cover-page shares outstanding (dei:EntityCommonStockSharesOutstanding), one observation
    per filing and as-of date. Companies with several share classes report one value per class
    in the same filing; those are summed to the company total. The same fact can also appear
    twice in one filing (with and without a frame), so identical entries are counted once."""
    try:
        entries = payload["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
    except KeyError:
        return []

    by_filing: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"shares": 0, "filed": None, "form": None}
    )
    seen: set[tuple[str, str, int]] = set()
    for entry in entries:
        value = entry.get("val")
        if not isinstance(value, int | float) or value <= 0:
            continue
        identity = (entry["accn"], entry["end"], int(value))
        if identity in seen:
            continue
        seen.add(identity)
        key = (entry["accn"], entry["end"])
        by_filing[key]["shares"] += int(value)
        by_filing[key]["filed"] = entry["filed"]
        by_filing[key]["form"] = entry.get("form")

    # Keep one observation per (as-of, filed) date pair; amendments filed the same day agree.
    observations: dict[tuple[date, date], SharesObservation] = {}
    for (_, end), info in sorted(by_filing.items()):
        as_of, filed = date.fromisoformat(end), date.fromisoformat(info["filed"])
        observations.setdefault(
            (as_of, filed),
            SharesObservation(
                as_of_date=as_of, filed_date=filed, shares=info["shares"], form=info["form"]
            ),
        )
    return sorted(observations.values(), key=lambda o: (o.filed_date, o.as_of_date))


class SecEdgarProvider(FundamentalsProvider):
    name: ClassVar[str] = "sec_edgar"

    def __init__(self) -> None:
        user_agent = get_settings().sec_user_agent
        if not user_agent:
            raise ProviderNotConfiguredError(
                "SEC_USER_AGENT is not set. SEC requires a contact, e.g. "
                "SEC_USER_AGENT='Breakout you@example.com' in .env."
            )
        self._client = http.new_client(user_agent)
        self._limiter = RateLimiter(get_redis(), self.name, per_second=REQUESTS_PER_SECOND)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _json(self, url: str) -> dict[str, Any] | None:
        response: httpx.Response = await http.get(self._client, url, limiter=self._limiter)
        if response.status_code == 404:
            return None
        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ProviderError(f"{url} did not return JSON") from exc
        return data

    async def company_identifiers(self) -> list[CompanyIdentifier]:
        payload = await self._json(TICKERS_URL)
        if payload is None:
            raise ProviderError(f"{TICKERS_URL} returned 404")
        return parse_company_tickers(payload)

    async def company_reference(self, cik: str) -> CompanyReference:
        payload = await self._json(SUBMISSIONS_URL.format(cik=pad_cik(cik)))
        if payload is None:
            return CompanyReference(cik=pad_cik(cik), name="", sic_code=None, sic_description=None)
        return parse_submissions(payload)

    async def shares_outstanding(self, cik: str) -> list[SharesObservation]:
        payload = await self._json(COMPANY_FACTS_URL.format(cik=pad_cik(cik)))
        return [] if payload is None else parse_shares_outstanding(payload)

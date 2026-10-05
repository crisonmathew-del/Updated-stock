"""Nasdaq Trader symbol directory: every security listed on Nasdaq, NYSE, NYSE American, NYSE
Arca, Cboe BZX and IEX, refreshed by Nasdaq several times a day. Free, no key.

Files: https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs
"""

from typing import ClassVar

from app.core.config import get_settings
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.providers import http
from app.providers.base import ListedSecurity, ProviderError, ReferenceProvider

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# otherlisted.txt "Exchange" codes.
OTHER_EXCHANGES = {"A": "AMEX", "N": "NYSE", "P": "ARCA", "Z": "BATS", "V": "IEX"}


def _rows(text: str, expected_header: list[str]) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ProviderError("Symbol directory file is empty")
    header = [h.strip() for h in lines[0].split("|")]
    if header[: len(expected_header)] != expected_header:
        raise ProviderError(f"Unexpected symbol directory header: {header}")
    rows = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        values = line.split("|")
        rows.append(dict(zip(header, (v.strip() for v in values), strict=False)))
    return rows


def parse_nasdaq_listed(text: str) -> list[ListedSecurity]:
    header = [
        "Symbol",
        "Security Name",
        "Market Category",
        "Test Issue",
        "Financial Status",
        "Round Lot Size",
        "ETF",
        "NextShares",
    ]
    return [
        ListedSecurity(
            symbol=row["Symbol"],
            name=row["Security Name"],
            exchange="NASDAQ",
            is_etf=row["ETF"] == "Y" or row.get("NextShares") == "Y",
            is_test_issue=row["Test Issue"] == "Y",
        )
        for row in _rows(text, header)
        if row.get("Symbol")
    ]


def parse_other_listed(text: str) -> list[ListedSecurity]:
    header = [
        "ACT Symbol",
        "Security Name",
        "Exchange",
        "CQS Symbol",
        "ETF",
        "Round Lot Size",
        "Test Issue",
        "NASDAQ Symbol",
    ]
    return [
        ListedSecurity(
            symbol=row["ACT Symbol"],
            name=row["Security Name"],
            exchange=OTHER_EXCHANGES.get(row["Exchange"], row["Exchange"]),
            is_etf=row["ETF"] == "Y",
            is_test_issue=row["Test Issue"] == "Y",
        )
        for row in _rows(text, header)
        if row.get("ACT Symbol")
    ]


class NasdaqTraderProvider(ReferenceProvider):
    name: ClassVar[str] = "nasdaq_trader"

    async def listed_securities(self) -> list[ListedSecurity]:
        limiter = RateLimiter(get_redis(), self.name, per_second=1, burst=2)
        user_agent = get_settings().sec_user_agent or "Breakout"
        async with http.new_client(user_agent) as client:
            nasdaq = await http.get(client, NASDAQ_LISTED_URL, limiter=limiter)
            other = await http.get(client, OTHER_LISTED_URL, limiter=limiter)
        for response in (nasdaq, other):
            if response.status_code != 200:
                raise ProviderError(f"{response.url} returned HTTP {response.status_code}")
        return parse_nasdaq_listed(nasdaq.text) + parse_other_listed(other.text)

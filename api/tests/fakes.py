"""In-memory providers for tests."""

from collections.abc import Sequence
from datetime import date
from typing import ClassVar

from app.providers.base import (
    CompanyIdentifier,
    CompanyReference,
    FetchResult,
    FundamentalsProvider,
    ListedSecurity,
    PriceHistory,
    PriceProvider,
    ReferenceProvider,
    SharesObservation,
)


class FakeReference(ReferenceProvider):
    name: ClassVar[str] = "fake_ref"

    def __init__(self, listed: list[ListedSecurity]):
        self.listed = listed

    async def listed_securities(self) -> list[ListedSecurity]:
        return list(self.listed)


class FakeFundamentals(FundamentalsProvider):
    name: ClassVar[str] = "fake_fund"

    def __init__(
        self,
        identifiers: list[CompanyIdentifier],
        references: dict[str, CompanyReference] | None = None,
        shares: dict[str, list[SharesObservation]] | None = None,
    ):
        self.identifiers = identifiers
        self.references = references or {}
        self.shares = shares or {}
        self.share_calls: list[str] = []

    async def company_identifiers(self) -> list[CompanyIdentifier]:
        return list(self.identifiers)

    async def company_reference(self, cik: str) -> CompanyReference:
        return self.references.get(cik, CompanyReference(cik, "", None, None))

    async def shares_outstanding(self, cik: str) -> list[SharesObservation]:
        self.share_calls.append(cik)
        return list(self.shares.get(cik, []))


class FakePrices(PriceProvider):
    """Serves fixed histories, filtered to the requested date range."""

    name: ClassVar[str] = "fake_px"

    def __init__(self, histories: dict[str, PriceHistory], errors: dict[str, str] | None = None):
        self.histories = histories
        self.errors = errors or {}
        self.requests: list[tuple[tuple[str, ...], date, date]] = []

    async def daily_history(self, symbols: Sequence[str], start: date, end: date) -> FetchResult:
        self.requests.append((tuple(symbols), start, end))
        found: dict[str, PriceHistory] = {}
        errors: dict[str, str] = {}
        for symbol in symbols:
            if symbol in self.errors:
                errors[symbol] = self.errors[symbol]
                continue
            history = self.histories.get(symbol)
            bars = [b for b in history.bars if start <= b.date <= end] if history else []
            if not bars or history is None:
                errors[symbol] = "no bars in range"
                continue
            actions = [a for a in history.actions if start <= a.ex_date <= end]
            found[symbol] = PriceHistory(symbol, bars, actions)
        return FetchResult(found, errors)

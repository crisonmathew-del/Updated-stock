"""In-memory providers for tests."""

from collections.abc import Sequence
from datetime import date
from typing import ClassVar

from app.providers.base import (
    Bar,
    CompanyFilings,
    CompanyFinancials,
    CompanyIdentifier,
    CompanyReference,
    CorporateActionRecord,
    FetchResult,
    FilingRecord,
    FilingsProvider,
    FundamentalsProvider,
    IndexEntry,
    InsiderTransaction,
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
        financials: dict[str, CompanyFinancials] | None = None,
        filings: dict[str, list[FilingRecord]] | None = None,
    ):
        self.identifiers = identifiers
        self.references = references or {}
        self.shares = shares or {}
        self.financials = financials or {}
        self.filings = filings or {}
        self.share_calls: list[str] = []
        self.financial_calls: list[str] = []
        self.filing_calls: list[str] = []

    async def company_identifiers(self) -> list[CompanyIdentifier]:
        return list(self.identifiers)

    async def company_reference(self, cik: str) -> CompanyReference:
        return self.references.get(cik, CompanyReference(cik, "", None, None))

    async def shares_outstanding(self, cik: str) -> list[SharesObservation]:
        self.share_calls.append(cik)
        return list(self.shares.get(cik, []))

    async def company_financials(self, cik: str) -> CompanyFinancials:
        self.financial_calls.append(cik)
        return self.financials.get(cik, CompanyFinancials([], list(self.shares.get(cik, []))))

    async def company_filings(self, cik: str) -> CompanyFilings:
        self.filing_calls.append(cik)
        reference = self.references.get(cik, CompanyReference(cik, "", None, None))
        return CompanyFilings(reference, list(self.filings.get(cik, [])))


class FakeFilings(FilingsProvider):
    """Daily indexes by date, Form 4 trades by index path, insider data sets by quarter."""

    name: ClassVar[str] = "fake_filings"

    def __init__(
        self,
        index: dict[date, list[IndexEntry]] | None = None,
        form4: dict[str, list[InsiderTransaction]] | None = None,
        quarters: dict[tuple[int, int], list[InsiderTransaction]] | None = None,
    ):
        self.index = index or {}
        self.form4 = form4 or {}
        self.quarters = quarters or {}
        self.index_calls: list[date] = []
        self.form4_calls: list[str] = []
        self.quarter_calls: list[tuple[int, int]] = []

    async def daily_index(self, day: date) -> list[IndexEntry] | None:
        self.index_calls.append(day)
        return self.index.get(day)

    async def insider_filing(self, entry: IndexEntry) -> list[InsiderTransaction]:
        self.form4_calls.append(entry.path)
        return list(self.form4.get(entry.path, []))

    async def insider_quarter(self, year: int, quarter: int) -> list[InsiderTransaction] | None:
        self.quarter_calls.append((year, quarter))
        found = self.quarters.get((year, quarter))
        return None if found is None else list(found)


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


def make_history(
    symbol: str,
    start: date,
    end: date,
    *,
    first_close: float = 100.0,
    step: float = 0.5,
    actions: list[CorporateActionRecord] | None = None,
) -> PriceHistory:
    """Synthetic bars on every NYSE session between `start` and `end`."""
    from app.core.calendar import sessions_between

    bars = []
    close = first_close
    for day in sessions_between(start, end):
        bars.append(Bar(day, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000))
        close += step
    return PriceHistory(symbol, bars, actions or [])

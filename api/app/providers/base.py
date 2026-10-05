"""Provider interfaces (spec §5.1) and the data types they exchange.

Adapters are chosen by the `*_PROVIDER` settings (see `app.providers.registry`), so swapping
yfinance for Massive, or SEC EDGAR for FMP, never touches the ingestion code. Each interface
declares only the methods built so far; later phases add methods as they need them (intraday
bars and snapshots in Phase 6, statements and estimates in Phase 3).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar


class ProviderError(Exception):
    """A provider failed in a way the caller should report rather than retry immediately."""


class RateLimitedError(ProviderError):
    """The provider is throttling us even after backing off."""


class ProviderNotConfiguredError(ProviderError):
    """The selected provider needs credentials or an adapter that isn't available yet."""


# --- Data types -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None


class ActionKind(StrEnum):
    SPLIT = "split"
    DIVIDEND = "dividend"


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    """`value` is the split ratio (new shares per old share) or the dividend per share."""

    ex_date: date
    kind: ActionKind
    value: float


@dataclass(frozen=True)
class PriceHistory:
    """Split-adjusted daily bars for one symbol, plus the splits/dividends in the range."""

    symbol: str
    bars: list[Bar]
    actions: list[CorporateActionRecord] = field(default_factory=list)


@dataclass(frozen=True)
class FetchResult:
    histories: dict[str, PriceHistory]
    errors: dict[str, str]


@dataclass(frozen=True, slots=True)
class ListedSecurity:
    """One row of an exchange symbol directory."""

    symbol: str
    name: str
    exchange: str
    is_etf: bool
    is_test_issue: bool


@dataclass(frozen=True, slots=True)
class CompanyIdentifier:
    symbol: str
    cik: str
    name: str
    exchange: str | None


@dataclass(frozen=True, slots=True)
class CompanyReference:
    cik: str
    name: str
    sic_code: str | None
    sic_description: str | None


@dataclass(frozen=True, slots=True)
class SharesObservation:
    """Shares outstanding as of `as_of_date`, made public on `filed_date`."""

    as_of_date: date
    filed_date: date
    shares: int
    form: str | None


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    size: int


@dataclass(frozen=True, slots=True)
class NewsItem:
    symbol: str
    published_at: datetime
    headline: str
    url: str
    source: str


# --- Interfaces -----------------------------------------------------------------------------


class ReferenceProvider(ABC):
    """Which securities are listed (the raw universe)."""

    name: ClassVar[str]

    @abstractmethod
    async def listed_securities(self) -> list[ListedSecurity]: ...

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release network clients. Adapters that hold none need not override this."""


class PriceProvider(ABC):
    """Daily bars and corporate actions."""

    name: ClassVar[str]

    @abstractmethod
    async def daily_history(self, symbols: Sequence[str], start: date, end: date) -> FetchResult:
        """Split-adjusted bars from `start` to `end` inclusive, one entry per symbol that
        returned data; symbols with no data or errors appear in `FetchResult.errors`."""

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release network clients. Adapters that hold none need not override this."""


class FundamentalsProvider(ABC):
    """Company identifiers, classification and share counts (statements arrive in Phase 3)."""

    name: ClassVar[str]

    @abstractmethod
    async def company_identifiers(self) -> list[CompanyIdentifier]: ...

    @abstractmethod
    async def company_reference(self, cik: str) -> CompanyReference: ...

    @abstractmethod
    async def shares_outstanding(self, cik: str) -> list[SharesObservation]: ...

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release network clients. Adapters that hold none need not override this."""


class StreamProvider(ABC):
    """Real-time trades (Phase 6)."""

    name: ClassVar[str]

    @abstractmethod
    def trades(self, symbols: Sequence[str]) -> AsyncIterator[Trade]: ...


class NewsProvider(ABC):
    """Company headlines (Phase 3/5)."""

    name: ClassVar[str]

    @abstractmethod
    async def company_news(self, symbol: str, since: date) -> list[NewsItem]: ...


class FilingsProvider(ABC):
    """Insider transactions (Form 4) and institutional holdings (13F), Phase 3."""

    name: ClassVar[str]

    @abstractmethod
    async def insider_transactions(self, cik: str, since: date) -> list[dict[str, object]]: ...

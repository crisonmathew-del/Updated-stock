"""Provider interfaces (spec §5.1) and the data types they exchange.

Adapters are chosen by the `*_PROVIDER` settings (see `app.providers.registry`), so swapping
yfinance for Massive, or SEC EDGAR for FMP, never touches the ingestion code. Each interface
declares only the methods built so far; later phases add methods as they need them (intraday
bars and snapshots in Phase 6, estimates and 13F holdings once a paid provider is connected).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar


class ProviderError(Exception):
    """A provider failed in a way the caller should report rather than retry immediately.
    `status` is the HTTP status when the failure was an HTTP response."""

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


class PeriodKind(StrEnum):
    QUARTER = "quarter"
    ANNUAL = "annual"


@dataclass(frozen=True, slots=True)
class FinancialPeriod:
    """One version of a fiscal period's figures, as known from `reported_date` (the filing
    date) until a later version replaces it. Restatements and later comparatives become new
    versions, so a calculation for date D uses the latest version with reported_date <= D.

    Money values are in `currency` units; EPS in currency per share as reported at the time
    (not adjusted for later splits). `derived` marks a quarter computed from year-to-date
    totals (usually Q4 = full year - nine months), which is approximate for EPS."""

    kind: PeriodKind
    period_start: date
    period_end: date
    reported_date: date
    fiscal_year: int | None
    fiscal_period: str | None  # Q1..Q4 or FY
    form: str | None
    accession: str | None
    currency: str | None
    eps_diluted: float | None = None
    eps_basic: float | None = None
    revenue: float | None = None
    net_income: float | None = None
    operating_income: float | None = None
    equity: float | None = None
    derived: bool = False


@dataclass(frozen=True)
class CompanyFinancials:
    """Everything we read from one company-facts document."""

    periods: list[FinancialPeriod]
    shares: list[SharesObservation]


@dataclass(frozen=True, slots=True)
class FilingRecord:
    """One filing from a company's submission history. `accepted_at` is the EDGAR acceptance
    time in US/Eastern; `items` are 8-K item numbers (e.g. "2.02" = results of operations)."""

    accession: str
    form: str
    filed: date
    accepted_at: datetime | None
    report_date: date | None
    items: tuple[str, ...]
    primary_document: str | None


@dataclass(frozen=True, slots=True)
class EarningsRelease:
    """A quarterly/annual results release. `timing` is before_open, during_session,
    after_close or unknown (relative to the regular session on `report_date`)."""

    report_date: date
    timing: str
    accession: str | None


@dataclass(frozen=True)
class CompanyFilings:
    reference: CompanyReference
    filings: list[FilingRecord]
    releases: list[EarningsRelease] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One line of an EDGAR daily index: a filing by (or about) the company with this CIK."""

    cik: str
    company: str
    form: str
    filed: date
    path: str  # edgar/data/<cik>/<accession>.txt


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """An open-market purchase (code P) or sale (code S) of common stock from Form 4.
    `seq` numbers the transactions within one filing."""

    accession: str
    seq: int
    issuer_cik: str
    filed: date
    transaction_date: date
    insider_cik: str
    insider_name: str
    role: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    code: str
    shares: float
    price: float | None


@dataclass(frozen=True, slots=True)
class Trade:
    """One print from the live feed (or a replayed one). `timestamp` is timezone-aware."""

    symbol: str
    timestamp: datetime
    price: float
    size: int


@dataclass(frozen=True, slots=True)
class MinuteBar:
    """A one-minute bar; `ts` is the minute's start, timezone-aware."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A stock's state right now: the last trade, today's regular-session open/high/low and
    volume so far, pre-market volume, and the previous close (spec §7.1 scans). Volumes are as
    the feed reports them (an IEX-only feed sees a small share of the market's volume)."""

    symbol: str
    ts: datetime | None
    last: float | None
    prev_close: float | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int = 0
    premarket_volume: int = 0


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
    """Company identifiers, classification, share counts, financial statements and the filing
    history (earnings release dates)."""

    name: ClassVar[str]

    @abstractmethod
    async def company_identifiers(self) -> list[CompanyIdentifier]: ...

    @abstractmethod
    async def company_reference(self, cik: str) -> CompanyReference: ...

    @abstractmethod
    async def shares_outstanding(self, cik: str) -> list[SharesObservation]: ...

    @abstractmethod
    async def company_financials(self, cik: str) -> CompanyFinancials:
        """Point-in-time quarterly and annual figures plus share counts (empty if unknown)."""

    @abstractmethod
    async def company_filings(self, cik: str) -> CompanyFilings:
        """Reference data and the full filing history, oldest first."""

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release network clients. Adapters that hold none need not override this."""


class StreamProvider(ABC):
    """The live feed (spec §5.1): trades for a changing set of symbols, and snapshots for the
    scans. `now()` is the feed's clock: wall time when live, the replayed moment in replay.

    `volume_share` is the share of the market's (consolidated) volume this feed reports: 1 for
    full SIP data and replays of it, a few % for IEX-only feeds. Intraday volume from a partial
    feed is scaled by it and every intraday volume check stays provisional until the close."""

    name: ClassVar[str]
    volume_share: float = 1.0
    partial_volume: bool = False

    @abstractmethod
    def trades(self) -> AsyncIterator[Trade]:
        """Prints for the subscribed symbols, until the feed ends (replay) or is closed."""

    @abstractmethod
    async def subscribe(self, symbols: Sequence[str]) -> None:
        """Replace the set of symbols whose trades `trades()` yields."""

    @abstractmethod
    async def snapshots(self, symbols: Sequence[str]) -> dict[str, Snapshot]:
        """The current snapshot of each symbol the feed knows."""

    @abstractmethod
    def now(self) -> datetime: ...

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release connections."""


class NewsProvider(ABC):
    """Company headlines (Phase 3/5)."""

    name: ClassVar[str]

    @abstractmethod
    async def company_news(self, symbol: str, since: date) -> list[NewsItem]: ...


class FilingsProvider(ABC):
    """The daily filing index and insider transactions (Form 4). Institutional holdings (13F)
    arrive with a paid provider."""

    name: ClassVar[str]

    @abstractmethod
    async def daily_index(self, day: date) -> list[IndexEntry] | None:
        """Every filing accepted on `day`, or None if there is no index (weekend/holiday or
        not published yet)."""

    @abstractmethod
    async def insider_filing(self, entry: IndexEntry) -> list[InsiderTransaction]:
        """Open-market purchases and sales in one Form 4 filing."""

    @abstractmethod
    async def insider_quarter(self, year: int, quarter: int) -> list[InsiderTransaction] | None:
        """Every open-market purchase and sale filed in a calendar quarter (bulk data set), or
        None if that quarter isn't published yet."""

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release network clients. Adapters that hold none need not override this."""

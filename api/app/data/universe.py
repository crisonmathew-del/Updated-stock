"""Universe builder (spec §5.3): which securities we track.

Weekly (and on demand):
1. Read the exchange symbol directories and keep common stock and ADRs on NYSE, Nasdaq and NYSE
   American, plus the fixed benchmark list.
2. Sync `tickers`: add new listings, refresh names/exchanges, and mark names that disappeared as
   inactive with a delisted date. Rows are never deleted, so backtests can still see them.
3. From SEC EDGAR (when SEC_USER_AGENT is set): CIKs, SIC codes and cover-page share counts.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.classify import universe_type
from app.models import SharesOutstanding, Ticker, TickerType
from app.providers.base import (
    FundamentalsProvider,
    ListedSecurity,
    ProviderError,
    ReferenceProvider,
)

log = get_logger(__name__)

REFERENCE_MAX_AGE = timedelta(days=30)
SEC_CONCURRENCY = 8
# If a directory download is truncated we must not mass-delist: refuse to apply a universe that
# is less than half the size of the current one.
MIN_UNIVERSE_RATIO = 0.5


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    name: str
    exchange: str
    type: TickerType
    is_benchmark: bool = False


BENCHMARKS: tuple[UniverseEntry, ...] = tuple(
    UniverseEntry(symbol, name, exchange, kind, is_benchmark=True)
    for symbol, name, exchange, kind in (
        ("SPY", "SPDR S&P 500 ETF Trust", "ARCA", TickerType.ETF),
        ("QQQ", "Invesco QQQ Trust, Series 1", "NASDAQ", TickerType.ETF),
        ("IWM", "iShares Russell 2000 ETF", "ARCA", TickerType.ETF),
        ("DIA", "SPDR Dow Jones Industrial Average ETF Trust", "ARCA", TickerType.ETF),
        ("^VIX", "Cboe Volatility Index", "INDEX", TickerType.INDEX),
        ("XLB", "Materials Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLC", "Communication Services Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLE", "Energy Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLF", "Financial Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLI", "Industrial Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLK", "Technology Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLP", "Consumer Staples Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLRE", "Real Estate Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLU", "Utilities Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLV", "Health Care Select Sector SPDR Fund", "ARCA", TickerType.ETF),
        ("XLY", "Consumer Discretionary Select Sector SPDR Fund", "ARCA", TickerType.ETF),
    )
)


class UniverseSanityError(RuntimeError):
    pass


@dataclass
class SyncResult:
    listed: int = 0
    universe: int = 0
    added: list[str] = field(default_factory=list)
    delisted: list[str] = field(default_factory=list)
    updated: int = 0


def plan_universe(listed: Sequence[ListedSecurity]) -> dict[str, UniverseEntry]:
    """Universe entries keyed by symbol: eligible listings plus every benchmark."""
    entries: dict[str, UniverseEntry] = {}
    for security in listed:
        kind = universe_type(security)
        if kind is not None:
            entries[security.symbol] = UniverseEntry(
                security.symbol, security.name, security.exchange, kind
            )
    for benchmark in BENCHMARKS:
        entries[benchmark.symbol] = benchmark
    return entries


async def sync_tickers(
    session: AsyncSession, entries: dict[str, UniverseEntry], today: date
) -> SyncResult:
    active = {t.symbol: t for t in (await session.scalars(select(Ticker).where(Ticker.active)))}
    current_stocks = sum(1 for t in active.values() if not t.is_benchmark)
    new_stocks = sum(1 for e in entries.values() if not e.is_benchmark)
    if current_stocks > 100 and new_stocks < current_stocks * MIN_UNIVERSE_RATIO:
        raise UniverseSanityError(
            f"The symbol directory lists {new_stocks} eligible stocks but {current_stocks} are "
            "active. Refusing to delist that many; the download may be incomplete."
        )

    result = SyncResult(universe=len(entries))
    for symbol, entry in entries.items():
        ticker = active.get(symbol)
        if ticker is None:
            session.add(
                Ticker(
                    symbol=symbol,
                    name=entry.name,
                    exchange=entry.exchange,
                    type=entry.type,
                    is_benchmark=entry.is_benchmark,
                    first_seen=today,
                    last_seen=today,
                )
            )
            result.added.append(symbol)
            continue
        changed = (ticker.name, ticker.exchange, ticker.type, ticker.is_benchmark) != (
            entry.name,
            entry.exchange,
            entry.type,
            entry.is_benchmark,
        )
        ticker.name, ticker.exchange, ticker.type = entry.name, entry.exchange, entry.type
        ticker.is_benchmark = entry.is_benchmark
        ticker.last_seen = today
        result.updated += int(changed)

    for symbol, ticker in active.items():
        if symbol not in entries:
            ticker.active = False
            ticker.delisted_date = ticker.last_seen
            result.delisted.append(symbol)

    await session.commit()
    return result


async def sync_identifiers(session: AsyncSession, fundamentals: FundamentalsProvider) -> int:
    """Attach SEC CIKs to active tickers. Returns how many tickers have a CIK afterwards."""
    ciks = {i.symbol: i.cik for i in await fundamentals.company_identifiers()}
    tickers = (await session.scalars(select(Ticker).where(Ticker.active))).all()
    for ticker in tickers:
        cik = ciks.get(ticker.symbol)
        if cik is not None:
            ticker.cik = cik
    await session.commit()
    return sum(1 for t in tickers if t.cik)


async def refresh_reference(
    session: AsyncSession,
    fundamentals: FundamentalsProvider,
    now: datetime,
    max_age: timedelta = REFERENCE_MAX_AGE,
) -> tuple[int, dict[str, str]]:
    """Fetch SIC codes for tickers whose reference data is missing or older than `max_age`.
    Returns (refreshed count, errors by symbol)."""
    stale_before = now - max_age
    tickers = (
        await session.scalars(
            select(Ticker).where(
                Ticker.active,
                Ticker.cik.is_not(None),
                (Ticker.reference_refreshed_at.is_(None))
                | (Ticker.reference_refreshed_at < stale_before),
            )
        )
    ).all()
    by_cik: dict[str, list[Ticker]] = {}
    for ticker in tickers:
        by_cik.setdefault(str(ticker.cik), []).append(ticker)

    semaphore = asyncio.Semaphore(SEC_CONCURRENCY)
    errors: dict[str, str] = {}

    async def one(cik: str, group: list[Ticker]) -> None:
        async with semaphore:
            try:
                reference = await fundamentals.company_reference(cik)
            except ProviderError as exc:
                for t in group:
                    errors[t.symbol] = str(exc)
                return
        for t in group:
            t.sic_code = reference.sic_code
            t.sic_description = reference.sic_description
            t.reference_refreshed_at = now

    await asyncio.gather(*(one(cik, group) for cik, group in by_cik.items()))
    await session.commit()
    return len(tickers) - len(errors), errors


async def refresh_shares(
    session: AsyncSession, fundamentals: FundamentalsProvider
) -> tuple[int, dict[str, str]]:
    """Store cover-page share counts for active common stocks. ADRs are skipped: their filings
    count underlying ordinary shares, not ADSs, so price × shares would be wrong."""
    tickers = (
        await session.scalars(
            select(Ticker).where(
                Ticker.active, Ticker.type == TickerType.COMMON, Ticker.cik.is_not(None)
            )
        )
    ).all()
    by_cik: dict[str, list[int]] = {}
    symbols: dict[int, str] = {}
    for ticker in tickers:
        by_cik.setdefault(str(ticker.cik), []).append(ticker.id)
        symbols[ticker.id] = ticker.symbol

    semaphore = asyncio.Semaphore(SEC_CONCURRENCY)
    errors: dict[str, str] = {}
    rows: list[dict[str, object]] = []

    async def one(cik: str, ticker_ids: list[int]) -> None:
        async with semaphore:
            try:
                observations = await fundamentals.shares_outstanding(cik)
            except ProviderError as exc:
                for tid in ticker_ids:
                    errors[symbols[tid]] = str(exc)
                return
        for tid in ticker_ids:
            rows.extend(
                {
                    "ticker_id": tid,
                    "as_of_date": o.as_of_date,
                    "filed_date": o.filed_date,
                    "shares": o.shares,
                    "form": o.form,
                    "source": fundamentals.name,
                }
                for o in observations
            )

    await asyncio.gather(*(one(cik, ids) for cik, ids in by_cik.items()))
    for start in range(0, len(rows), 5000):
        chunk = rows[start : start + 5000]
        stmt = insert(SharesOutstanding).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["ticker_id", "as_of_date", "filed_date"],
                set_={"shares": stmt.excluded.shares, "form": stmt.excluded.form},
            )
        )
    await session.commit()
    return len(tickers) - len(errors), errors


async def build_universe(
    session: AsyncSession,
    reference: ReferenceProvider,
    fundamentals: FundamentalsProvider | None,
    today: date,
    stats: dict[str, object],
) -> SyncResult:
    """The full weekly rebuild. `stats` is filled in for the job-run record."""
    listed = await reference.listed_securities()
    entries = plan_universe(listed)
    result = await sync_tickers(session, entries, today)
    result.listed = len(listed)
    stats.update(
        listed=result.listed,
        universe=result.universe,
        added=len(result.added),
        delisted=len(result.delisted),
        updated=result.updated,
    )
    log.info(
        "universe.synced", **{k: stats[k] for k in ("listed", "universe", "added", "delisted")}
    )

    if fundamentals is None:
        stats["sec"] = "skipped: SEC_USER_AGENT is not set"
        return result

    stats["with_cik"] = await sync_identifiers(session, fundamentals)
    refreshed, reference_errors = await refresh_reference(session, fundamentals, datetime.now(UTC))
    stats["reference_refreshed"] = refreshed
    shares, share_errors = await refresh_shares(session, fundamentals)
    stats["shares_refreshed"] = shares
    stats["sec_errors"] = len(reference_errors) + len(share_errors)
    return result

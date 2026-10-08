"""Load fundamentals into the database.

- `refresh_companies`: statements (every point-in-time version), share counts, the earnings
  calendar (past releases + the estimated next one) and SIC codes, one company per request
  pair. A company's rows are replaced as a whole, so a refresh is idempotent.
- `process_filing_index`: reads the daily filing indexes, returns the companies that filed
  something that can change their numbers, and stores the day's Form 4 insider trades.
- `load_insider_quarters`: SEC's quarterly bulk insider data sets for history.

Only `providers.base` interfaces are used, so a paid provider slots in through the registry.
"""

import asyncio
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.data.universe import upsert_shares
from app.fundamentals.earnings import estimate_next_release
from app.models import (
    EarningsEvent,
    FundamentalsAnnual,
    FundamentalsQuarterly,
    InsiderTransaction,
    JobRun,
    Ticker,
    TickerType,
)
from app.providers.base import (
    CompanyFilings,
    CompanyFinancials,
    FilingsProvider,
    FundamentalsProvider,
    IndexEntry,
    PeriodKind,
    ProviderError,
)
from app.providers.base import InsiderTransaction as InsiderTrade

log = get_logger(__name__)

CONCURRENCY = 8
BATCH_COMPANIES = 100
CHUNK_ROWS = 2000
# Filings that can change a company's statements, share count or earnings dates.
REFRESH_FORMS = frozenset(
    {"10-Q", "10-K", "20-F", "40-F", "8-K", "6-K"}
    | {f"{form}/A" for form in ("10-Q", "10-K", "20-F", "40-F", "8-K", "6-K")}
)
INSIDER_FORMS = frozenset({"4", "4/A"})
# The nightly run reads at most this many days of filing indexes; a longer gap (or the first
# run) triggers a full refresh of every company instead.
MAX_INDEX_DAYS = 10


@dataclass
class Company:
    """One SEC registrant and the listed tickers that belong to it (share classes)."""

    cik: str
    ticker_ids: list[int] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    # Share counts are stored for common stock only (an ADR's filings count ordinary shares).
    common_ids: list[int] = field(default_factory=list)


async def load_companies(
    session: AsyncSession,
    *,
    symbols: Sequence[str] | None = None,
    ciks: Iterable[str] | None = None,
    never_refreshed: bool = False,
) -> list[Company]:
    """Active stocks with a CIK, grouped by company. Filters combine with OR; none = all."""
    stmt = select(Ticker.id, Ticker.symbol, Ticker.cik, Ticker.type).where(
        Ticker.active,
        Ticker.cik.is_not(None),
        Ticker.type.in_([TickerType.COMMON, TickerType.ADR]),
    )
    filters = []
    if symbols is not None:
        filters.append(Ticker.symbol.in_([s.upper() for s in symbols]))
    if ciks is not None:
        filters.append(Ticker.cik.in_(list(ciks)))
    if never_refreshed:
        filters.append(Ticker.fundamentals_refreshed_at.is_(None))
    if filters:
        stmt = stmt.where(or_(*filters))
    companies: dict[str, Company] = {}
    for ticker_id, symbol, cik, kind in (await session.execute(stmt.order_by(Ticker.cik))).all():
        company = companies.setdefault(str(cik), Company(str(cik)))
        company.ticker_ids.append(ticker_id)
        company.symbols.append(symbol)
        if kind == TickerType.COMMON:
            company.common_ids.append(ticker_id)
    return list(companies.values())


async def issuer_map(session: AsyncSession) -> dict[str, list[int]]:
    """CIK → active stock ticker ids (for matching insider filings to tickers)."""
    rows = (
        await session.execute(
            select(Ticker.cik, Ticker.id).where(
                Ticker.active,
                Ticker.cik.is_not(None),
                Ticker.type.in_([TickerType.COMMON, TickerType.ADR]),
            )
        )
    ).all()
    out: dict[str, list[int]] = defaultdict(list)
    for cik, ticker_id in rows:
        out[str(cik)].append(ticker_id)
    return dict(out)


def _statement_rows(
    ticker_ids: Sequence[int], financials: CompanyFinancials, kind: PeriodKind, source: str
) -> list[dict[str, Any]]:
    return [
        {
            "ticker_id": ticker_id,
            "period_end": p.period_end,
            "reported_date": p.reported_date,
            "period_start": p.period_start,
            "fiscal_year": p.fiscal_year,
            "fiscal_period": p.fiscal_period,
            "form": p.form,
            "accession": p.accession,
            "currency": p.currency,
            "eps_diluted": p.eps_diluted,
            "eps_basic": p.eps_basic,
            "revenue": p.revenue,
            "net_income": p.net_income,
            "operating_income": p.operating_income,
            "equity": p.equity,
            "derived": p.derived,
            "source": source,
        }
        for p in financials.periods
        if p.kind == kind
        for ticker_id in ticker_ids
    ]


def _calendar_rows(
    ticker_ids: Sequence[int], filings: CompanyFilings, today: date, source: str
) -> list[dict[str, Any]]:
    reported = {r.report_date: r for r in filings.releases}
    rows = [
        {
            "ticker_id": ticker_id,
            "report_date": r.report_date,
            "status": "reported",
            "timing": r.timing,
            "accession": r.accession,
            "source": source,
        }
        for r in reported.values()
        for ticker_id in ticker_ids
    ]
    upcoming = estimate_next_release(reported, today)
    if upcoming is not None and upcoming not in reported:
        rows.extend(
            {
                "ticker_id": ticker_id,
                "report_date": upcoming,
                "status": "estimated",
                "timing": "unknown",
                "accession": None,
                "source": "estimate",
            }
            for ticker_id in ticker_ids
        )
    return rows


async def _insert(session: AsyncSession, model: Any, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), CHUNK_ROWS):
        await session.execute(model.__table__.insert(), rows[start : start + CHUNK_ROWS])


async def _store_companies(
    session: AsyncSession,
    source: str,
    done: Sequence[tuple[Company, tuple[CompanyFinancials, CompanyFilings]]],
    *,
    today: date,
    now: datetime,
) -> dict[str, int]:
    """Replace these companies' statements and calendar, add their share counts, and commit.
    Returns the row counts."""
    ids = [tid for company, _ in done for tid in company.ticker_ids]
    quarterly: list[dict[str, Any]] = []
    annual: list[dict[str, Any]] = []
    calendar: list[dict[str, Any]] = []
    shares: list[dict[str, object]] = []
    for company, (financials, filings) in done:
        quarterly += _statement_rows(company.ticker_ids, financials, PeriodKind.QUARTER, source)
        annual += _statement_rows(company.ticker_ids, financials, PeriodKind.ANNUAL, source)
        calendar += _calendar_rows(company.ticker_ids, filings, today, source)
        shares += [
            {
                "ticker_id": tid,
                "as_of_date": o.as_of_date,
                "filed_date": o.filed_date,
                "shares": o.shares,
                "form": o.form,
                "source": source,
            }
            for o in financials.shares
            for tid in company.common_ids
        ]
    for model in (FundamentalsQuarterly, FundamentalsAnnual, EarningsEvent):
        await session.execute(delete(model).where(model.ticker_id.in_(ids)))
    await _insert(session, FundamentalsQuarterly, quarterly)
    await _insert(session, FundamentalsAnnual, annual)
    await _insert(session, EarningsEvent, calendar)
    await upsert_shares(session, shares)
    for company, (_, filings) in done:
        reference = filings.reference
        values: dict[str, Any] = {"fundamentals_refreshed_at": now}
        if reference.sic_code:
            values |= {
                "sic_code": reference.sic_code,
                "sic_description": reference.sic_description,
                "reference_refreshed_at": now,
            }
        await session.execute(
            update(Ticker).where(Ticker.id.in_(company.ticker_ids)).values(**values)
        )
    await session.commit()
    return {
        "companies": len(done),
        "quarterly_rows": len(quarterly),
        "annual_rows": len(annual),
        "earnings_dates": len(calendar),
    }


async def refresh_companies(
    session: AsyncSession,
    provider: FundamentalsProvider,
    companies: Sequence[Company],
    *,
    today: date,
    now: datetime,
    stats: dict[str, Any],
) -> dict[str, str]:
    """Fetch and store every company's statements, share counts and earnings calendar.
    Returns errors by symbol; a failing company keeps its previous data."""
    semaphore = asyncio.Semaphore(CONCURRENCY)
    errors: dict[str, str] = {}
    totals: dict[str, int] = defaultdict(int)

    async def fetch(company: Company) -> tuple[CompanyFinancials, CompanyFilings] | None:
        async with semaphore:
            try:
                financials = await provider.company_financials(company.cik)
                filings = await provider.company_filings(company.cik)
            except ProviderError as exc:
                for symbol in company.symbols:
                    errors[symbol] = str(exc)
                return None
        return financials, filings

    for start in range(0, len(companies), BATCH_COMPANIES):
        batch = companies[start : start + BATCH_COMPANIES]
        results = await asyncio.gather(*(fetch(c) for c in batch))
        done = [(c, r) for c, r in zip(batch, results, strict=True) if r is not None]
        if not done:
            continue
        try:
            counts = await _store_companies(session, provider.name, done, today=today, now=now)
        except DBAPIError:
            # One company's unstorable figures must not cost the others theirs: store them one
            # by one, and report (and keep the previous data of) the ones that fail.
            await session.rollback()
            counts = defaultdict(int)
            for item in done:
                try:
                    one = await _store_companies(
                        session, provider.name, [item], today=today, now=now
                    )
                except DBAPIError as exc:
                    await session.rollback()
                    reason = str(exc.orig).splitlines()[0][:200]
                    log.warning("fundamentals.store_failed", cik=item[0].cik, error=reason)
                    for symbol in item[0].symbols:
                        errors[symbol] = f"Could not store its figures: {reason}"
                    continue
                for key, value in one.items():
                    counts[key] += value
        for key, value in counts.items():
            totals[key] += value
        log.info("fundamentals.batch", done=start + len(batch), of=len(companies))

    stats.update(
        companies=totals["companies"],
        quarterly_rows=totals["quarterly_rows"],
        annual_rows=totals["annual_rows"],
        earnings_dates=totals["earnings_dates"],
        company_errors=len(errors),
    )
    if errors:
        stats["company_error_sample"] = dict(sorted(errors.items())[:5])
    return errors


# --- Insider transactions -------------------------------------------------------------------


async def store_insider_trades(
    session: AsyncSession,
    trades: Sequence[InsiderTrade],
    issuers: dict[str, list[int]],
    source: str,
) -> int:
    """Replace the stored rows of every filing in `trades` (re-reading a filing never
    duplicates it). Trades of issuers outside the universe are dropped."""
    rows = [
        {
            "accession": t.accession,
            "seq": t.seq,
            "ticker_id": ticker_id,
            "filed_date": t.filed,
            "transaction_date": t.transaction_date,
            "insider_cik": t.insider_cik,
            "insider_name": t.insider_name[:160],
            "role": t.role[:160],
            "is_director": t.is_director,
            "is_officer": t.is_officer,
            "is_ten_percent_owner": t.is_ten_percent_owner,
            "code": t.code,
            "shares": t.shares,
            "price": t.price,
            "source": source,
        }
        for t in trades
        for ticker_id in issuers.get(t.issuer_cik, [])
    ]
    # One row per key even if a filing reached us twice (the last copy wins).
    rows = list({(r["accession"], r["seq"], r["ticker_id"]): r for r in rows}.values())
    accessions = sorted({str(r["accession"]) for r in rows})
    for start in range(0, len(accessions), CHUNK_ROWS):
        chunk = accessions[start : start + CHUNK_ROWS]
        await session.execute(
            delete(InsiderTransaction).where(InsiderTransaction.accession.in_(chunk))
        )
    await _insert(session, InsiderTransaction, rows)
    await session.commit()
    return len(rows)


def quarters_back(today: date, count: int) -> list[tuple[int, int]]:
    """The `count` calendar quarters before the current one, oldest first."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(count):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        out.append((year, quarter))
    return out[::-1]


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start = date(year, 3 * quarter - 2, 1)
    end = date(year + quarter // 4, (3 * quarter) % 12 + 1, 1) - timedelta(days=1)
    return start, end


async def loaded_quarters(session: AsyncSession, source: str) -> set[tuple[int, int]]:
    """Quarters that already have bulk-data-set rows."""
    days = (
        await session.scalars(
            select(InsiderTransaction.filed_date)
            .where(InsiderTransaction.source == source)
            .distinct()
        )
    ).all()
    return {(d.year, (d.month - 1) // 3 + 1) for d in days}


async def load_insider_quarters(
    session: AsyncSession,
    provider: FilingsProvider,
    quarters: Sequence[tuple[int, int]],
    issuers: dict[str, list[int]],
    stats: dict[str, Any],
) -> None:
    """Load the bulk data sets that aren't stored yet. A quarter that isn't published yet is
    skipped (the nightly Form 4 reading covers recent filings)."""
    source = f"{provider.name}_bulk"[:16]
    have = await loaded_quarters(session, source)
    loaded, rows = [], 0
    for year, quarter in quarters:
        if (year, quarter) in have:
            continue
        trades = await provider.insider_quarter(year, quarter)
        if trades is None:
            continue
        start, end = _quarter_bounds(year, quarter)
        rows += await store_insider_trades(
            session, [t for t in trades if start <= t.filed <= end], issuers, source
        )
        loaded.append(f"{year}Q{quarter}")
    stats.update(insider_quarters_loaded=loaded, insider_bulk_rows=rows)


# --- Daily filing index ---------------------------------------------------------------------


@dataclass
class IndexResult:
    days_read: list[date]
    through: date | None  # the latest day whose index exists
    refresh_ciks: set[str]
    form4_filings: int
    insider_rows: int


async def process_filing_index(
    session: AsyncSession,
    provider: FilingsProvider,
    days: Sequence[date],
    issuers: dict[str, list[int]],
) -> IndexResult:
    """Read each day's index: collect universe companies whose filings may change their data,
    and store the insider trades from that day's Form 4s. A day without an index (holiday, or
    not published yet) is skipped; `through` only advances past days that had one."""
    result = IndexResult([], None, set(), 0, 0)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    for day in sorted(days):
        entries = await provider.daily_index(day)
        if entries is None:
            continue
        result.days_read.append(day)
        result.through = day
        form4s: dict[str, IndexEntry] = {}
        for entry in entries:
            if entry.cik not in issuers:
                continue
            if entry.form in REFRESH_FORMS:
                result.refresh_ciks.add(entry.cik)
            elif entry.form in INSIDER_FORMS:
                # A Form 4 is listed under the issuer and under each reporting owner, each in
                # its own folder (edgar/data/<cik>/<accession>.txt). When an owner is also in
                # the universe (Lantheus reporting its sales of Perspective Therapeutics), the
                # same filing passes this filter twice: read it once, by its file name.
                form4s.setdefault(PurePosixPath(entry.path).name, entry)

        async def read(entry: IndexEntry) -> list[InsiderTrade]:
            async with semaphore:
                try:
                    return await provider.insider_filing(entry)
                except ProviderError as exc:
                    log.warning("fundamentals.form4_failed", path=entry.path, error=str(exc))
                    return []

        batches = await asyncio.gather(*(read(e) for e in form4s.values()))
        trades = [t for batch in batches for t in batch]
        result.form4_filings += len(form4s)
        result.insider_rows += await store_insider_trades(session, trades, issuers, provider.name)
    return result


async def last_index_through(session: AsyncSession) -> date | None:
    """The latest filing-index day a successful fundamentals run has read."""
    value = await session.scalar(
        select(JobRun.stats["index_through"].astext)
        .where(
            JobRun.job_name == "fundamentals",
            JobRun.status == "succeeded",
            JobRun.stats["index_through"].astext.is_not(None),
        )
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    return date.fromisoformat(value) if value else None


def index_days(last_through: date | None, yesterday: date, bootstrap_days: int) -> list[date]:
    """Weekdays to read: after `last_through` up to `yesterday`, at most MAX_INDEX_DAYS of
    them (the most recent). With no history, the last `bootstrap_days` calendar days."""
    start = (
        yesterday - timedelta(days=bootstrap_days - 1)
        if last_through is None
        else last_through + timedelta(days=1)
    )
    days = [
        start + timedelta(days=i)
        for i in range((yesterday - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    return days if last_through is None else days[-MAX_INDEX_DAYS:]


def needs_full_refresh(last_through: date | None, yesterday: date) -> bool:
    """Too many unread index days (or none read ever): refresh every company instead."""
    if last_through is None:
        return True
    weekdays = sum(
        1
        for i in range(1, (yesterday - last_through).days + 1)
        if (last_through + timedelta(days=i)).weekday() < 5
    )
    return weekdays > MAX_INDEX_DAYS

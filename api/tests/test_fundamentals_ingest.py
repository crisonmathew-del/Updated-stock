"""The fundamentals job: full and nightly loads, the filing index, insider trades and the
estimated earnings calendar. Providers are fakes fed with the real-shaped SEC fixtures."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import jobs
from app.data.universe import plan_universe, sync_tickers
from app.fundamentals.earnings import estimate_next_release
from app.fundamentals.ingest import index_days, needs_full_refresh, quarters_back
from app.models import (
    EarningsEvent,
    FundamentalsAnnual,
    FundamentalsQuarterly,
    InsiderTransaction,
    SharesOutstanding,
    Ticker,
)
from app.providers.base import CompanyFinancials, CompanyReference, IndexEntry
from app.providers.base import InsiderTransaction as Trade
from app.providers.sec_edgar import parse_company_financials
from app.providers.sec_filings import earnings_releases, parse_filing_history, parse_form4
from tests.fakes import FakeFilings, FakeFundamentals
from tests.test_universe import listed

FIXTURES = Path(__file__).parent / "fixtures" / "providers"
NORTHWIND, FJORD = "0001234567", "0007654321"
FORM4_PATH = "edgar/data/1234567/0001234567-23-000031.txt"
OWNER_FORM4_PATH = "edgar/data/7654321/0001234567-23-000031.txt"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def northwind_releases() -> list[date]:
    filings = parse_filing_history(
        load("submissions_filings.json"), [load("submissions_filings_page1.json")]
    )
    return [r.report_date for r in earnings_releases(filings)]


# --- Pure helpers ---------------------------------------------------------------------------


def test_releases_are_one_per_day_with_their_timing() -> None:
    filings = parse_filing_history(
        load("submissions_filings.json"), [load("submissions_filings_page1.json")]
    )
    releases = earnings_releases(filings)
    assert [(r.report_date, r.timing) for r in releases] == [
        (date(2022, 4, 28), "after_close"),
        (date(2022, 7, 28), "after_close"),
        (date(2022, 10, 27), "during_session"),
        (date(2023, 2, 9), "before_open"),
        (date(2023, 4, 27), "after_close"),
    ]


def test_next_release_is_52_weeks_after_the_same_quarter_last_year() -> None:
    releases = northwind_releases()
    # Latest release 2023-04-27; 2022-04-28 + 52 weeks = 2023-04-27 is not after it, so the
    # next is 2022-07-28 + 52 weeks = Thursday 2023-07-27.
    assert estimate_next_release(releases, date(2023, 5, 12)) == date(2023, 7, 27)
    # Only releases known by then count: as of 2022-08-01 there is no year of history yet,
    # so the estimate is the latest release plus one quarter (2022-07-28 + 91 days).
    assert estimate_next_release(releases, date(2022, 8, 1)) == date(2022, 10, 27)


def test_next_release_edge_cases() -> None:
    assert estimate_next_release([], date(2023, 5, 1)) is None
    # A Saturday estimate moves to Monday: 2022-11-05 (Sat) + 91 days = 2023-02-04 (Sat).
    assert estimate_next_release([date(2022, 11, 5)], date(2022, 12, 1)) == date(2023, 2, 6)
    # Expected 2022-04-11 (2022-01-10 + 91 days). Nine days late: imminent (next weekday).
    assert estimate_next_release([date(2022, 1, 10)], date(2022, 4, 20)) == date(2022, 4, 21)
    # Months late: the cadence is broken, so no estimate rather than a stale guess.
    assert estimate_next_release([date(2022, 1, 10)], date(2022, 9, 1)) is None


def test_index_days_skip_weekends_and_cap_the_catch_up() -> None:
    monday = date(2023, 5, 15)
    assert index_days(date(2023, 5, 11), monday, 30) == [
        date(2023, 5, 12),
        date(2023, 5, 15),
    ]
    caught_up = index_days(date(2023, 4, 1), monday, 30)
    assert len(caught_up) == 10
    assert caught_up[-1] == monday
    bootstrap = index_days(None, monday, 7)  # 2023-05-09 .. 2023-05-15
    assert bootstrap == [
        date(2023, 5, 9),
        date(2023, 5, 10),
        date(2023, 5, 11),
        date(2023, 5, 12),
        date(2023, 5, 15),
    ]
    assert needs_full_refresh(None, monday)
    assert not needs_full_refresh(date(2023, 5, 1), monday)  # 10 weekdays behind
    assert needs_full_refresh(date(2023, 4, 28), monday)  # 11


def test_quarters_back_cross_year_boundaries() -> None:
    assert quarters_back(date(2023, 5, 15), 3) == [(2022, 3), (2022, 4), (2023, 1)]


# --- The job --------------------------------------------------------------------------------


def form4_trades() -> list[Trade]:
    text = (FIXTURES / "form4_purchase.txt").read_text()
    return parse_form4(text, accession="0001234567-23-000031", filed=date(2023, 5, 12))


def fake_sec() -> FakeFundamentals:
    reference = CompanyReference(NORTHWIND, "Northwind Tools Inc.", "3559", "Machinery")
    filings = parse_filing_history(
        load("submissions_filings.json"), [load("submissions_filings_page1.json")]
    )
    return FakeFundamentals(
        identifiers=[],
        references={NORTHWIND: reference},
        financials={
            NORTHWIND: parse_company_financials(load("companyfacts_financials.json")),
            FJORD: parse_company_financials(load("companyfacts_ifrs_20f.json")),
        },
        filings={NORTHWIND: filings},
        releases={NORTHWIND: earnings_releases(filings)},
    )


def fake_filings() -> FakeFilings:
    entry = IndexEntry(NORTHWIND, "Northwind Tools Inc.", "4", date(2023, 5, 12), FORM4_PATH)
    # The same Form 4 is also listed under the reporting owner, in the owner's folder. Here the
    # owner is a universe company itself (Fjord), so the row passes the universe filter too.
    owner_row = IndexEntry(FJORD, "Fjord Holdings ASA", "4", date(2023, 5, 12), OWNER_FORM4_PATH)
    bulk = Trade(
        accession="0001234567-23-000002",
        seq=1,
        issuer_cik=NORTHWIND,
        filed=date(2023, 2, 15),
        transaction_date=date(2023, 2, 14),
        insider_cik="0001900001",
        insider_name="Roe Richard",
        role="Director",
        is_director=True,
        is_officer=False,
        is_ten_percent_owner=False,
        code="P",
        shares=1000,
        price=38.0,
    )
    return FakeFilings(
        index={
            date(2023, 5, 11): [],
            date(2023, 5, 12): [entry, owner_row],
            date(2023, 5, 15): [
                IndexEntry(FJORD, "Fjord Holdings ASA", "6-K", date(2023, 5, 15), "edgar/x.txt")
            ],
        },
        form4={FORM4_PATH: form4_trades(), OWNER_FORM4_PATH: form4_trades()},
        quarters={(2023, 1): [bulk]},
    )


async def seed_universe(db: AsyncSession) -> dict[str, int]:
    """GOOGL + GOOG stand in for Northwind's two share classes, TSM for the Fjord ADR."""
    await sync_tickers(db, plan_universe(listed("GOOGL", "GOOG", "TSM", "AAPL")), date(2023, 5, 1))
    await db.execute(
        update(Ticker).where(Ticker.symbol.in_(["GOOGL", "GOOG"])).values(cik=NORTHWIND)
    )
    await db.execute(update(Ticker).where(Ticker.symbol == "TSM").values(cik=FJORD))
    await db.commit()
    return {t.symbol: t.id for t in (await db.scalars(select(Ticker))).all()}


@pytest.fixture
def market_day(monkeypatch: pytest.MonkeyPatch) -> Callable[[date], None]:
    def set_day(day: date) -> None:
        monkeypatch.setattr(jobs, "market_today", lambda: day)

    return set_day


async def count(db: AsyncSession, model: Any, ticker_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(model)
    if ticker_id is not None:
        stmt = stmt.where(model.ticker_id == ticker_id)
    return int(await db.scalar(stmt) or 0)


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_first_run_loads_everything(
    db: AsyncSession, market_day: Callable[[date], None]
) -> None:
    ids = await seed_universe(db)
    sec, filings = fake_sec(), fake_filings()
    market_day(date(2023, 5, 16))  # Tuesday: the indexes up to Monday 2023-05-15 exist

    stats = await jobs.fundamentals_job("cli", fundamentals=sec, filings=filings)

    assert stats["mode"] == "full"  # never run before
    assert stats["index_through"] == "2023-05-15"
    assert stats["form4_filings"] == 1  # the owner's copy of the same filing is not re-read
    assert stats["companies"] == 2
    assert stats["insider_quarters_loaded"] == ["2023Q1"]
    assert sorted(sec.financial_calls) == [NORTHWIND, FJORD]  # one call per company

    googl, goog, tsm = ids["GOOGL"], ids["GOOG"], ids["TSM"]
    # Every version of every period, for both share classes.
    assert await count(db, FundamentalsQuarterly, googl) == 7
    assert await count(db, FundamentalsAnnual, googl) == 3
    assert await count(db, FundamentalsQuarterly, goog) == 7
    assert await count(db, FundamentalsAnnual, tsm) == 4
    assert await count(db, FundamentalsQuarterly, tsm) == 0
    # Cover-page shares: common stock only (an ADR's filings count ordinary shares).
    assert await count(db, SharesOutstanding, googl) == 1
    assert await count(db, SharesOutstanding, tsm) == 0

    calendar = (
        await db.execute(
            select(EarningsEvent.report_date, EarningsEvent.status, EarningsEvent.timing)
            .where(EarningsEvent.ticker_id == googl)
            .order_by(EarningsEvent.report_date)
        )
    ).all()
    assert [tuple(r) for r in calendar] == [
        (date(2022, 4, 28), "reported", "after_close"),
        (date(2022, 7, 28), "reported", "after_close"),
        (date(2022, 10, 27), "reported", "during_session"),
        (date(2023, 2, 9), "reported", "before_open"),
        (date(2023, 4, 27), "reported", "after_close"),
        (date(2023, 7, 27), "estimated", "unknown"),
    ]

    trades = (
        await db.execute(
            select(InsiderTransaction.transaction_date, InsiderTransaction.shares)
            .where(InsiderTransaction.ticker_id == googl)
            .order_by(InsiderTransaction.transaction_date)
        )
    ).all()
    assert [tuple(t) for t in trades] == [
        (date(2023, 2, 14), 1000.0),  # bulk data set
        (date(2023, 5, 10), 5000.0),  # nightly Form 4
        (date(2023, 5, 11), 2500.0),
    ]
    assert await count(db, InsiderTransaction, tsm) == 0

    refreshed = (await db.execute(select(Ticker).where(Ticker.id == googl))).scalar_one()
    assert refreshed.fundamentals_refreshed_at is not None
    assert refreshed.sic_code == "3559"
    aapl = (await db.execute(select(Ticker).where(Ticker.id == ids["AAPL"]))).scalar_one()
    assert aapl.fundamentals_refreshed_at is None  # no CIK: nothing to load


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_nightly_run_refreshes_only_companies_that_filed(
    db: AsyncSession, market_day: Callable[[date], None]
) -> None:
    ids = await seed_universe(db)
    market_day(date(2023, 5, 13))  # Saturday: reads up to Friday 2023-05-12
    await jobs.fundamentals_job("cli", fundamentals=fake_sec(), filings=fake_filings())

    sec, filings = fake_sec(), fake_filings()
    market_day(date(2023, 5, 16))
    stats = await jobs.fundamentals_job("cli", fundamentals=sec, filings=filings)

    assert stats["mode"] == "nightly"
    assert stats["index_days"] == ["2023-05-15"]
    assert stats["index_through"] == "2023-05-15"
    assert sec.financial_calls == [FJORD]  # its 6-K was in Monday's index
    assert stats["insider_quarters_loaded"] == []  # 2023Q1 is already stored
    # Re-reading and re-storing never duplicates rows.
    assert await count(db, FundamentalsQuarterly, ids["GOOGL"]) == 7
    assert await count(db, InsiderTransaction, ids["GOOGL"]) == 3


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_missing_index_is_retried_the_next_night(
    db: AsyncSession, market_day: Callable[[date], None]
) -> None:
    await seed_universe(db)
    market_day(date(2023, 5, 13))
    await jobs.fundamentals_job("cli", fundamentals=fake_sec(), filings=fake_filings())

    filings = fake_filings()
    del filings.index[date(2023, 5, 15)]  # Monday's index isn't published yet
    market_day(date(2023, 5, 16))
    stats = await jobs.fundamentals_job("cli", fundamentals=fake_sec(), filings=filings)
    assert stats["index_through"] == "2023-05-12"

    filings = fake_filings()
    market_day(date(2023, 5, 17))  # Tuesday's index doesn't exist in the fake either
    stats = await jobs.fundamentals_job("cli", fundamentals=fake_sec(), filings=filings)
    assert filings.index_calls == [date(2023, 5, 15), date(2023, 5, 16)]
    assert stats["index_through"] == "2023-05-15"


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_failed_company_keeps_its_previous_data(
    db: AsyncSession, market_day: Callable[[date], None]
) -> None:
    ids = await seed_universe(db)
    market_day(date(2023, 5, 16))
    await jobs.fundamentals_job("cli", fundamentals=fake_sec(), filings=fake_filings())

    failing = fake_sec()
    failing.errors[NORTHWIND] = "GET companyfacts failed: HTTP 503"
    stats = await jobs.fundamentals_job(
        "cli", symbols=["GOOGL"], fundamentals=failing, filings=fake_filings()
    )
    assert stats["mode"] == "symbols"
    assert stats["company_errors"] == 1
    assert stats["company_error_sample"]["GOOGL"] == "GET companyfacts failed: HTTP 503"
    assert await count(db, FundamentalsQuarterly, ids["GOOGL"]) == 7


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_a_company_whose_figures_cannot_be_stored_does_not_stop_the_others(
    db: AsyncSession, market_day: Callable[[date], None]
) -> None:
    # Real filings carry surprises (a fiscal year of 43646 once failed the whole load). When
    # a batch can't be stored, its companies are stored one by one and only the bad one fails.
    ids = await seed_universe(db)
    market_day(date(2023, 5, 16))
    sec = fake_sec()
    fjord = sec.financials[FJORD]
    sec.financials[FJORD] = CompanyFinancials(
        [replace(p, fiscal_period="TOOLONG") for p in fjord.periods], fjord.shares
    )
    stats = await jobs.fundamentals_job("cli", fundamentals=sec, filings=fake_filings())
    assert stats["company_errors"] == 1
    assert stats["company_error_sample"]["TSM"].startswith(
        "Could not store its figures: value too long"
    )
    assert stats["companies"] == 1
    assert await count(db, FundamentalsQuarterly, ids["GOOGL"]) == 7
    assert await count(db, FundamentalsAnnual, ids["TSM"]) == 0


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_without_ciks_the_job_explains_what_to_run_first(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL")), date(2023, 5, 1))
    stats = await jobs.fundamentals_job("cli", fundamentals=fake_sec(), filings=fake_filings())
    assert stats["skipped"] == "No stocks with a CIK yet: run the universe job first."

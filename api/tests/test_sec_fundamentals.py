"""SEC parsers: company facts → point-in-time periods, filing history, daily index, Form 4 and
the quarterly insider data sets. Expected values are worked out by hand from the fixtures."""

import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.calendar import MARKET_TZ
from app.providers.base import FinancialPeriod, IndexEntry, PeriodKind
from app.providers.sec_edgar import parse_company_financials
from app.providers.sec_facts import parse_financials
from app.providers.sec_filings import (
    accession_from_path,
    extra_pages,
    is_earnings_release,
    parse_acceptance,
    parse_daily_index,
    parse_filing_history,
    parse_form4,
    parse_insider_dataset,
    release_timing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "providers"
F1, F2, F3, F4 = (
    "0001234567-22-000030",  # 10-Q Q3 2022, filed 2022-11-04
    "0001234567-23-000005",  # 10-K FY2022, filed 2023-02-24
    "0001234567-23-000012",  # 10-Q Q1 2023, filed 2023-05-05
    "0001234567-23-000020",  # 10-K/A FY2022 (restated revenue and EPS), filed 2023-06-15
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def versions(periods: list[FinancialPeriod], kind: PeriodKind, end: date) -> list[FinancialPeriod]:
    return [p for p in periods if p.kind == kind and p.period_end == end]


@pytest.fixture(scope="module")
def northwind() -> list[FinancialPeriod]:
    return parse_financials(load("companyfacts_financials.json"))


# --- Company facts --------------------------------------------------------------------------


def test_quarters_found_in_ten_qs_keep_their_filing_date(northwind: list[FinancialPeriod]) -> None:
    [q3] = versions(northwind, PeriodKind.QUARTER, date(2022, 9, 30))
    assert q3.period_start == date(2022, 7, 1)
    assert q3.reported_date == date(2022, 11, 4)
    assert (q3.fiscal_year, q3.fiscal_period) == (2022, "Q3")
    assert (q3.revenue, q3.eps_diluted) == (120_000_000, 0.60)
    assert (q3.form, q3.accession, q3.currency, q3.derived) == ("10-Q", F1, "USD", False)


def test_comparative_quarters_are_labelled_one_fiscal_year_back(
    northwind: list[FinancialPeriod],
) -> None:
    # Q3 2021 only appears as the comparative column of the Q3 2022 10-Q.
    [q3_prior] = versions(northwind, PeriodKind.QUARTER, date(2021, 9, 30))
    assert (q3_prior.fiscal_year, q3_prior.fiscal_period) == (2021, "Q3")
    assert (q3_prior.revenue, q3_prior.eps_diluted) == (95_000_000, 0.48)
    assert q3_prior.reported_date == date(2022, 11, 4)


def test_q4_is_full_year_minus_nine_months_and_follows_the_restatement(
    northwind: list[FinancialPeriod],
) -> None:
    first, restated = versions(northwind, PeriodKind.QUARTER, date(2022, 12, 31))
    # 10-K: revenue 460 - 330 = 130 (M); EPS 2.30 - 1.65 = 0.65. Equity is the year-end balance.
    assert first.period_start == date(2022, 10, 1)
    assert (first.fiscal_year, first.fiscal_period) == (2022, "Q4")
    assert first.reported_date == date(2023, 2, 24)
    assert (first.revenue, first.eps_diluted, first.equity) == (130_000_000, 0.65, 400_000_000)
    assert first.derived
    assert (first.form, first.accession) == ("10-K", F2)
    # 10-K/A: revenue 455 - 330 = 125; EPS 2.25 - 1.65 = 0.60. The Q1 10-Q in between repeated
    # the same year-end equity, so it made no new version.
    assert restated.reported_date == date(2023, 6, 15)
    assert (restated.revenue, restated.eps_diluted) == (125_000_000, 0.60)
    assert restated.derived
    assert (restated.form, restated.accession) == ("10-K/A", F4)


def test_derived_prior_year_q4_uses_the_comparative_nine_months(
    northwind: list[FinancialPeriod],
) -> None:
    [q4_prior] = versions(northwind, PeriodKind.QUARTER, date(2021, 12, 31))
    # 380 - 280 = 100 (M); EPS 1.80 - 1.35 = 0.45; known once the 10-K gave the full year.
    assert q4_prior.reported_date == date(2023, 2, 24)
    assert (q4_prior.revenue, q4_prior.eps_diluted, q4_prior.equity) == (
        100_000_000,
        0.45,
        350_000_000,
    )
    assert (q4_prior.fiscal_year, q4_prior.fiscal_period) == (2021, "Q4")


def test_revenue_falls_back_to_the_newer_concept_name(northwind: list[FinancialPeriod]) -> None:
    # The Q1 2023 10-Q reports revenue as RevenueFromContractWithCustomerExcludingAssessedTax.
    [q1] = versions(northwind, PeriodKind.QUARTER, date(2023, 3, 31))
    assert (q1.fiscal_year, q1.fiscal_period) == (2023, "Q1")
    assert q1.revenue == 125_000_000
    assert (q1.eps_diluted, q1.net_income, q1.operating_income, q1.equity) == (
        0.62,
        12_400_000,
        18_000_000,
        412_000_000,
    )
    [q1_prior] = versions(northwind, PeriodKind.QUARTER, date(2022, 3, 31))
    assert (q1_prior.fiscal_year, q1_prior.fiscal_period) == (2022, "Q1")
    assert (q1_prior.revenue, q1_prior.equity) == (100_000_000, None)


def test_annual_periods_keep_every_version(northwind: list[FinancialPeriod]) -> None:
    first, restated = versions(northwind, PeriodKind.ANNUAL, date(2022, 12, 31))
    assert first.period_start == date(2022, 1, 1)
    assert (first.fiscal_year, first.fiscal_period) == (2022, "FY")
    assert (first.revenue, first.eps_diluted, first.net_income, first.equity) == (
        460_000_000,
        2.30,
        46_000_000,
        400_000_000,
    )
    assert not first.derived
    assert restated.reported_date == date(2023, 6, 15)
    assert (restated.revenue, restated.eps_diluted, restated.net_income) == (
        455_000_000,
        2.25,
        46_000_000,
    )
    # The prior year was re-filed unchanged in the 10-K/A: still one version.
    [prior] = versions(northwind, PeriodKind.ANNUAL, date(2021, 12, 31))
    assert (prior.fiscal_year, prior.fiscal_period, prior.revenue) == (2021, "FY", 380_000_000)


def test_no_quarter_is_derived_across_a_six_month_gap(northwind: list[FinancialPeriod]) -> None:
    # Q1 2022 (3 months) and 9M 2022 share a start but are 6 months apart: no fake "Q2/Q3".
    ends = {p.period_end for p in northwind if p.kind == PeriodKind.QUARTER}
    assert ends == {
        date(2021, 9, 30),
        date(2021, 12, 31),
        date(2022, 3, 31),
        date(2022, 9, 30),
        date(2022, 12, 31),
        date(2023, 3, 31),
    }


def test_versions_are_the_point_in_time_view(northwind: list[FinancialPeriod]) -> None:
    """As of any date, the latest version filed by then is exactly what had been published."""

    def known(as_of: date, end: date) -> FinancialPeriod | None:
        found = [p for p in versions(northwind, PeriodKind.ANNUAL, end) if p.reported_date <= as_of]
        return found[-1] if found else None

    assert known(date(2023, 2, 23), date(2022, 12, 31)) is None
    first = known(date(2023, 6, 14), date(2022, 12, 31))
    assert first is not None
    assert first.revenue == 460_000_000
    latest = known(date(2023, 6, 15), date(2022, 12, 31))
    assert latest is not None
    assert latest.revenue == 455_000_000
    assert all(p.reported_date >= p.period_end for p in northwind)


def test_ifrs_twenty_f_filer_has_annual_figures_in_its_current_currency() -> None:
    periods = parse_financials(load("companyfacts_ifrs_20f.json"))
    assert {p.kind for p in periods} == {PeriodKind.ANNUAL}
    assert {p.currency for p in periods} == {"EUR"}
    # The 2019 USD revenue belongs to the old currency and is ignored.
    assert date(2019, 12, 31) not in {p.period_end for p in periods}

    fy21 = versions(periods, PeriodKind.ANNUAL, date(2021, 12, 31))
    assert [(p.fiscal_year, p.revenue, p.eps_diluted) for p in fy21] == [(2021, 1.6e9, 0.80)]
    first, second = versions(periods, PeriodKind.ANNUAL, date(2022, 12, 31))
    assert (first.reported_date, first.net_income) == (date(2023, 3, 30), None)
    # The next 20-F added net income and equity for 2022: a new, fuller version.
    assert (second.reported_date, second.net_income, second.equity) == (
        date(2024, 3, 28),
        220_000_000,
        1_300_000_000,
    )
    [fy23] = versions(periods, PeriodKind.ANNUAL, date(2023, 12, 31))
    assert (fy23.fiscal_year, fy23.fiscal_period, fy23.form) == (2023, "FY", "20-F")
    assert (fy23.revenue, fy23.eps_diluted, fy23.net_income, fy23.equity) == (
        2.6e9,
        1.50,
        300_000_000,
        1_500_000_000,
    )


def test_company_financials_include_cover_page_shares() -> None:
    financials = parse_company_financials(load("companyfacts_financials.json"))
    assert [(s.as_of_date, s.shares) for s in financials.shares] == [
        (date(2023, 4, 28), 20_000_000)
    ]
    assert len(financials.periods) == 10


def test_documents_without_financial_facts_give_nothing() -> None:
    assert parse_financials(load("companyfacts_no_dei.json")) == []
    assert parse_financials({"cik": 1, "facts": {}}) == []


# --- Filing history and earnings releases ---------------------------------------------------


def test_filing_history_merges_pages_dedupes_and_sorts_oldest_first() -> None:
    submissions = load("submissions_filings.json")
    filings = parse_filing_history(submissions, [load("submissions_filings_page1.json")])

    assert len(filings) == 12  # 9 recent + 4 older, one of which repeats a recent filing
    assert [f.filed for f in filings] == sorted(f.filed for f in filings)
    releases = [f for f in filings if is_earnings_release(f)]
    assert [r.filed for r in releases] == [
        date(2022, 4, 28),
        date(2022, 7, 28),
        date(2022, 10, 27),
        date(2023, 2, 9),
        date(2023, 4, 27),
    ]  # the 8-K/A amendment and the item 5.02 8-K are not releases
    first = releases[-1]
    assert first.items == ("2.02", "9.01")
    assert first.accepted_at == datetime(2023, 4, 27, 16, 5, 12, tzinfo=MARKET_TZ)
    assert first.report_date == date(2023, 4, 27)


def test_extra_pages_older_than_the_history_window_are_skipped() -> None:
    submissions = load("submissions_filings.json")
    assert extra_pages(submissions) == [
        "CIK0001234567-submissions-001.json",
        "CIK0001234567-submissions-002.json",
    ]
    assert extra_pages(submissions, since=date(2015, 1, 1)) == [
        "CIK0001234567-submissions-001.json"
    ]


@pytest.mark.parametrize(
    ("accepted", "timing"),
    [
        ("2023-02-09T07:45:03.000Z", "before_open"),
        ("2022-10-27T12:00:00.000Z", "during_session"),
        ("2023-04-27T16:05:12.000Z", "after_close"),
        ("2023-04-27T16:00:00.000Z", "after_close"),
        ("2023-04-27T09:29:59.000Z", "before_open"),
        (None, "unknown"),
    ],
)
def test_release_timing_reads_the_acceptance_clock_as_eastern(
    accepted: str | None, timing: str
) -> None:
    assert release_timing(parse_acceptance(accepted)) == timing


# --- Daily index and Form 4 -----------------------------------------------------------------


def test_daily_index_rows_are_parsed_and_headers_skipped() -> None:
    entries = parse_daily_index((FIXTURES / "master.20230512.idx").read_text())
    assert len(entries) == 5
    assert entries[1] == IndexEntry(
        cik="0001234567",
        company="Northwind Tools Inc.",
        form="4",
        filed=date(2023, 5, 12),
        path="edgar/data/1234567/0001234567-23-000031.txt",
    )
    assert entries[0].form == "SC 13G/A"
    assert accession_from_path(entries[1].path) == "0001234567-23-000031"


def test_form4_keeps_open_market_trades_of_the_first_reporting_owner() -> None:
    text = (FIXTURES / "form4_purchase.txt").read_text()
    trades = parse_form4(text, accession="0001234567-23-000031", filed=date(2023, 5, 12))

    assert [(t.seq, t.transaction_date, t.code, t.shares, t.price) for t in trades] == [
        (1, date(2023, 5, 10), "P", 5000.0, 41.2),
        (2, date(2023, 5, 11), "P", 2500.0, 41.85),
    ]  # the option exercise (code M) and the derivative table are ignored
    first = trades[0]
    assert (first.issuer_cik, first.insider_cik, first.insider_name) == (
        "0001234567",
        "0001876543",
        "Doe Jane",
    )
    assert first.role == "Chief Executive Officer"
    assert (first.is_director, first.is_officer, first.is_ten_percent_owner) == (True, True, False)
    assert first.filed == date(2023, 5, 12)


def test_form4_without_an_ownership_document_gives_nothing() -> None:
    day = date(2023, 5, 12)
    assert parse_form4("<SEC-DOCUMENT>no xml</SEC-DOCUMENT>", accession="x", filed=day) == []
    assert parse_form4("<ownershipDocument><broken", accession="x", filed=day) == []


def zipped_dataset() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in (FIXTURES / "insider_dataset").iterdir():
            archive.write(path, path.name)
    return buffer.getvalue()


def test_insider_dataset_matches_the_form4_reading_of_the_same_filing() -> None:
    trades = parse_insider_dataset(zipped_dataset())
    by_accession = {(t.accession, t.seq): t for t in trades}

    # Same filing as the Form 4 fixture: sequence follows the data set's row key, not file order.
    jane_first = by_accession[("0001234567-23-000031", 1)]
    assert (jane_first.transaction_date, jane_first.code, jane_first.shares) == (
        date(2023, 5, 10),
        "P",
        5000.0,
    )
    assert (jane_first.price, jane_first.filed) == (41.2, date(2023, 5, 12))
    assert jane_first.role == "Chief Executive Officer"
    assert jane_first.insider_cik == "0001876543"  # first reporting owner, not the trust
    assert by_accession[("0001234567-23-000031", 2)].shares == 2500.0

    director = by_accession[("0001234567-23-000035", 1)]
    assert (director.role, director.is_director, director.is_officer) == ("Director", True, False)
    fund = by_accession[("0001234567-23-000036", 1)]
    assert (fund.code, fund.role, fund.is_ten_percent_owner) == ("S", "10% owner", True)
    # Option exercises (M) and the Form 3 are left out.
    assert len(trades) == 4


def test_a_dataset_missing_a_table_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\n")
    with pytest.raises(ValueError, match=r"REPORTINGOWNER\.TSV is missing"):
        parse_insider_dataset(buffer.getvalue())

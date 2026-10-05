"""Live smoke tests against the real keyless sources. Deselected by default; run with
`uv run pytest -m network` when the network allows these hosts."""

from datetime import date, timedelta

import pytest

from app.providers.base import PeriodKind
from app.providers.nasdaq_trader import NasdaqTraderProvider
from app.providers.sec_edgar import SecEdgarProvider, SecFilingsProvider
from app.providers.sec_filings import is_earnings_release, release_timing
from app.providers.yfinance_dev import YFinanceDevProvider

pytestmark = [pytest.mark.network, pytest.mark.usefixtures("clean_redis")]


async def test_nasdaq_trader_lists_thousands_of_securities() -> None:
    rows = await NasdaqTraderProvider().listed_securities()
    symbols = {r.symbol for r in rows}
    assert len(rows) > 8000
    assert {"AAPL", "SPY", "BRK.B"} <= symbols


async def test_sec_edgar_maps_apple_and_reports_shares() -> None:
    sec = SecEdgarProvider()
    try:
        ids = {i.symbol: i.cik for i in await sec.company_identifiers()}
        assert ids["AAPL"] == "0000320193"
        reference = await sec.company_reference("320193")
        assert reference.sic_code == "3571"
        shares = await sec.shares_outstanding("320193")
        assert shares
        assert shares[-1].shares > 10_000_000_000
    finally:
        await sec.aclose()


async def test_sec_edgar_gives_apple_point_in_time_financials() -> None:
    sec = SecEdgarProvider()
    try:
        financials = await sec.company_financials("320193")
    finally:
        await sec.aclose()
    quarters = [p for p in financials.periods if p.kind == PeriodKind.QUARTER]
    annuals = [p for p in financials.periods if p.kind == PeriodKind.ANNUAL]
    assert len({p.period_end for p in quarters}) > 40
    assert any(p.derived and p.fiscal_period == "Q4" for p in quarters)
    assert max(p.revenue or 0 for p in annuals) > 300e9
    assert all(p.reported_date >= p.period_end for p in financials.periods)
    assert financials.shares


async def test_apple_releases_results_after_the_close() -> None:
    """Checks the assumption that the submissions API's acceptance clock is US/Eastern."""
    sec = SecEdgarProvider()
    try:
        filings = await sec.company_filings("320193")
    finally:
        await sec.aclose()
    releases = [f for f in filings.filings if is_earnings_release(f)]
    assert len(releases) >= 12
    timings = [release_timing(r.accepted_at) for r in releases[-8:]]
    assert timings.count("after_close") >= 6, timings


async def test_sec_daily_index_and_insider_data() -> None:
    sec = SecFilingsProvider()
    try:
        index = await sec.daily_index(date(2024, 5, 1))
        assert index is not None
        assert len(index) > 1000
        form4s = [e for e in index if e.form == "4"]
        assert form4s
        await sec.insider_filing(form4s[0])  # parses without error (may hold no P/S trades)
        assert await sec.daily_index(date(2024, 5, 4)) is None  # a Saturday
        quarter = await sec.insider_quarter(2024, 1)
        assert quarter is not None
        assert len(quarter) > 10_000
        assert {t.code for t in quarter} == {"P", "S"}
    finally:
        await sec.aclose()


async def test_yahoo_returns_recent_daily_bars() -> None:
    end = date.today()
    result = await YFinanceDevProvider().daily_history(
        ["AAPL", "SPY"], end - timedelta(days=14), end
    )
    assert set(result.histories) == {"AAPL", "SPY"}
    assert len(result.histories["SPY"].bars) >= 5

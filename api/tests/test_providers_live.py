"""Live smoke tests against the real keyless sources. Deselected by default; run with
`uv run pytest -m network` when the network allows these hosts."""

from datetime import date, timedelta

import pytest

from app.providers.nasdaq_trader import NasdaqTraderProvider
from app.providers.sec_edgar import SecEdgarProvider
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


async def test_yahoo_returns_recent_daily_bars() -> None:
    end = date.today()
    result = await YFinanceDevProvider().daily_history(
        ["AAPL", "SPY"], end - timedelta(days=14), end
    )
    assert set(result.histories) == {"AAPL", "SPY"}
    assert len(result.histories["SPY"].bars) >= 5

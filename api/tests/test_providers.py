import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.data.classify import SecurityClass, classify, universe_type
from app.models.ticker import TickerType
from app.providers.base import ActionKind, Bar, CorporateActionRecord, ListedSecurity, ProviderError
from app.providers.nasdaq_trader import parse_nasdaq_listed, parse_other_listed
from app.providers.sec_edgar import (
    pad_cik,
    parse_company_tickers,
    parse_shares_outstanding,
    parse_submissions,
    sec_to_symbol,
)
from app.providers.yfinance_dev import parse_history_frame, to_yahoo

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_text(name))


# --- Nasdaq Trader --------------------------------------------------------------------------


def test_parses_nasdaq_listed_and_skips_the_footer() -> None:
    rows = parse_nasdaq_listed(fixture_text("nasdaqlisted.txt"))

    assert len(rows) == 14
    assert rows[0] == ListedSecurity(
        symbol="AAPL",
        name="Apple Inc. - Common Stock",
        exchange="NASDAQ",
        is_etf=False,
        is_test_issue=False,
    )
    by_symbol = {r.symbol: r for r in rows}
    assert by_symbol["QQQ"].is_etf
    assert by_symbol["ZVZZT"].is_test_issue


def test_parses_other_listed_and_maps_exchange_codes() -> None:
    rows = {r.symbol: r for r in parse_other_listed(fixture_text("otherlisted.txt"))}

    assert rows["A"].exchange == "NYSE"
    assert rows["SEB"].exchange == "AMEX"
    assert rows["SPY"].exchange == "ARCA"
    assert rows["CBOE"].exchange == "BATS"
    assert rows["BRK.B"].name == "Berkshire Hathaway Inc. Class B Common Stock"


def test_rejects_a_file_with_an_unexpected_header() -> None:
    with pytest.raises(ProviderError, match="Unexpected symbol directory header"):
        parse_nasdaq_listed("Ticker|Name\nAAPL|Apple\n")


# --- Security classification ----------------------------------------------------------------

EXPECTED_CLASSES = {
    "AAPL": SecurityClass.COMMON,
    "GOOGL": SecurityClass.COMMON,
    "GOOG": SecurityClass.COMMON,  # "Class C Capital Stock"
    "ASML": SecurityClass.ADR,  # New York Registry Shares
    "PDD": SecurityClass.ADR,
    "QQQ": SecurityClass.ETF,
    "ZVZZT": SecurityClass.TEST,
    "CCCX": SecurityClass.COMMON,  # SPAC Class A ordinary shares
    "CCCXU": SecurityClass.UNIT,
    "CCCXW": SecurityClass.WARRANT,
    "GDSTR": SecurityClass.RIGHT,
    "LBRDP": SecurityClass.PREFERRED,
    "OXLCZ": SecurityClass.NOTE,
    "SPOT": SecurityClass.COMMON,
    "A": SecurityClass.COMMON,
    "BRK.A": SecurityClass.COMMON,
    "BRK.B": SecurityClass.COMMON,
    "TSM": SecurityClass.ADR,
    "PBR.A": SecurityClass.ADR,
    "SPY": SecurityClass.ETF,
    "XLK": SecurityClass.ETF,
    "ABR$D": SecurityClass.PREFERRED,
    "EPD": SecurityClass.COMMON,  # MLP common units are equity
    "ADX": SecurityClass.FUND,  # closed-end fund
    "SEB": SecurityClass.COMMON,
    "CBOE": SecurityClass.COMMON,
    "NTEST": SecurityClass.TEST,
    "AAM.U": SecurityClass.UNIT,  # name mentions its warrant; still a unit
    "AAM.WS": SecurityClass.WARRANT,
    "BAM": SecurityClass.COMMON,  # "Class A Limited Voting Shares"
    "O": SecurityClass.COMMON,
}


def all_fixture_rows() -> dict[str, ListedSecurity]:
    rows = parse_nasdaq_listed(fixture_text("nasdaqlisted.txt"))
    rows += parse_other_listed(fixture_text("otherlisted.txt"))
    return {r.symbol: r for r in rows}


def test_every_fixture_row_is_classified_as_expected() -> None:
    rows = all_fixture_rows()
    assert set(rows) == set(EXPECTED_CLASSES)
    mismatches = {
        symbol: (classify(row), EXPECTED_CLASSES[symbol])
        for symbol, row in rows.items()
        if classify(row) is not EXPECTED_CLASSES[symbol]
    }
    assert mismatches == {}


def test_universe_keeps_common_and_adrs_on_the_three_exchanges_only() -> None:
    rows = all_fixture_rows()
    universe = {s: universe_type(r) for s, r in rows.items() if universe_type(r) is not None}

    assert universe == {
        "AAPL": TickerType.COMMON,
        "GOOGL": TickerType.COMMON,
        "GOOG": TickerType.COMMON,
        "ASML": TickerType.ADR,
        "PDD": TickerType.ADR,
        "CCCX": TickerType.COMMON,
        "SPOT": TickerType.COMMON,
        "A": TickerType.COMMON,
        "BRK.A": TickerType.COMMON,
        "BRK.B": TickerType.COMMON,
        "TSM": TickerType.ADR,
        "PBR.A": TickerType.ADR,
        "EPD": TickerType.COMMON,
        "SEB": TickerType.COMMON,
        "BAM": TickerType.COMMON,
        "O": TickerType.COMMON,
    }  # CBOE is common stock but listed on Cboe BZX, outside NYSE/Nasdaq/NYSE American.


# --- SEC EDGAR ------------------------------------------------------------------------------


def test_company_tickers_map_to_dot_symbols_and_padded_ciks() -> None:
    ids = {
        i.symbol: i for i in parse_company_tickers(fixture_json("company_tickers_exchange.json"))
    }

    assert ids["BRK.B"].cik == "0001067983"
    assert ids["AAPL"].cik == "0000320193"
    assert ids["GOOG"].cik == ids["GOOGL"].cik
    assert "" not in ids
    assert sec_to_symbol("brk-a") == "BRK.A"
    assert pad_cik(320193) == pad_cik("0000320193") == "0000320193"


def test_submissions_give_sic_classification() -> None:
    apple = parse_submissions(fixture_json("submissions_aapl.json"))
    assert (apple.cik, apple.sic_code, apple.sic_description) == (
        "0000320193",
        "3571",
        "Electronic Computers",
    )
    blank = parse_submissions(fixture_json("submissions_no_sic.json"))
    assert (blank.sic_code, blank.sic_description) == (None, None)


def test_shares_outstanding_keeps_filing_dates_and_dedupes() -> None:
    observations = parse_shares_outstanding(fixture_json("companyfacts_single_class.json"))

    assert [(o.as_of_date, o.filed_date, o.shares, o.form) for o in observations] == [
        (date(2024, 7, 26), date(2024, 8, 2), 15_204_137_000, "10-Q"),
        (date(2024, 10, 18), date(2024, 11, 1), 15_115_823_000, "10-K"),
        (date(2025, 1, 17), date(2025, 1, 31), 15_037_874_000, "10-Q"),
    ]


def test_multi_class_share_counts_are_summed_and_zeros_ignored() -> None:
    observations = parse_shares_outstanding(fixture_json("companyfacts_multi_class.json"))

    assert len(observations) == 1
    assert observations[0].shares == 5_833_000_000 + 861_000_000 + 5_426_000_000


def test_filers_without_cover_page_shares_give_no_observations() -> None:
    assert parse_shares_outstanding(fixture_json("companyfacts_no_dei.json")) == []


# --- yfinance (dev) -------------------------------------------------------------------------


def yahoo_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    index = pd.DatetimeIndex([r.pop("Date") for r in rows], name="Date").tz_localize(
        "America/New_York"
    )
    return pd.DataFrame(rows, index=index)


def test_parses_yahoo_history_with_actions() -> None:
    frame = yahoo_frame(
        [
            {
                "Date": "2024-06-07",
                "Open": 1200.5,
                "High": 1215.0,
                "Low": 1190.25,
                "Close": 1208.88,
                "Adj Close": 120.8,
                "Volume": 41_000_000,
                "Dividends": 0.0,
                "Stock Splits": 0.0,
            },
            {
                "Date": "2024-06-10",
                "Open": 120.37,
                "High": 123.1,
                "Low": 117.01,
                "Close": 121.79,
                "Adj Close": 121.7,
                "Volume": 314_162_700,
                "Dividends": 0.01,
                "Stock Splits": 10.0,
            },
            {
                "Date": "2024-06-11",
                "Open": np.nan,
                "High": np.nan,
                "Low": np.nan,
                "Close": np.nan,
                "Adj Close": np.nan,
                "Volume": np.nan,
                "Dividends": 0.0,
                "Stock Splits": 0.0,
            },
        ]
    )

    history = parse_history_frame("NVDA", frame)

    assert history.bars == [
        Bar(date(2024, 6, 7), 1200.5, 1215.0, 1190.25, 1208.88, 41_000_000),
        Bar(date(2024, 6, 10), 120.37, 123.1, 117.01, 121.79, 314_162_700),
    ]
    assert history.actions == [
        CorporateActionRecord(date(2024, 6, 10), ActionKind.SPLIT, 10.0),
        CorporateActionRecord(date(2024, 6, 10), ActionKind.DIVIDEND, 0.01),
    ]


def test_session_dates_come_from_exchange_time_not_utc() -> None:
    # Midnight in New York is 04:00/05:00 UTC; converting to UTC first would still be the same
    # date, but a naive UTC index at 23:00 must not shift either.
    frame = yahoo_frame(
        [
            {
                "Date": "2025-03-10",
                "Open": 1.0,
                "High": 1.0,
                "Low": 1.0,
                "Close": 1.0,
                "Volume": 5,
                "Dividends": 0.0,
                "Stock Splits": 0.0,
            }
        ]
    )
    assert parse_history_frame("X", frame).bars[0].date == date(2025, 3, 10)


def test_yahoo_symbols() -> None:
    assert to_yahoo("BRK.B") == "BRK-B"
    assert to_yahoo("^VIX") == "^VIX"
    assert to_yahoo("AAPL") == "AAPL"

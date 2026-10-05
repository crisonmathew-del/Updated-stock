from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.market_cap import market_cap, update_latest_market_caps
from app.data.universe import (
    BENCHMARKS,
    UniverseSanityError,
    build_universe,
    plan_universe,
    sync_tickers,
)
from app.models import CorporateAction, DailyBar, SharesOutstanding, Ticker
from app.providers.base import (
    CompanyIdentifier,
    CompanyReference,
    ListedSecurity,
    SharesObservation,
)
from tests.fakes import FakeFundamentals, FakeReference
from tests.test_providers import all_fixture_rows

MONDAY = date(2026, 9, 28)
NEXT_MONDAY = date(2026, 10, 5)


def listed(*symbols: str) -> list[ListedSecurity]:
    rows = all_fixture_rows()
    return [rows[s] for s in symbols]


def test_plan_has_eligible_stocks_plus_every_benchmark() -> None:
    plan = plan_universe(list(all_fixture_rows().values()))

    benchmarks = {s for s, e in plan.items() if e.is_benchmark}
    assert benchmarks == {b.symbol for b in BENCHMARKS}
    assert len(benchmarks) == 16  # SPY QQQ IWM DIA ^VIX + 11 sector SPDRs
    stocks = {s for s, e in plan.items() if not e.is_benchmark}
    assert {"AAPL", "BRK.B", "TSM", "EPD"} <= stocks
    assert not {"QQQ.X", "ABR$D", "CCCXU", "ADX", "CBOE", "ZVZZT"} & stocks
    assert plan["SPY"].exchange == "ARCA"  # benchmark metadata wins over the directory row


@pytest.mark.integration
async def test_sync_adds_updates_and_delists_without_deleting(db: AsyncSession) -> None:
    first = await sync_tickers(db, plan_universe(listed("AAPL", "A", "TSM")), MONDAY)
    assert sorted(first.added) == sorted(["AAPL", "A", "TSM", *(b.symbol for b in BENCHMARKS)])

    second = await sync_tickers(db, plan_universe(listed("AAPL", "TSM")), NEXT_MONDAY)

    assert second.added == []
    assert second.delisted == ["A"]
    agilent = await db.scalar(select(Ticker).where(Ticker.symbol == "A"))
    assert agilent is not None
    assert (agilent.active, agilent.delisted_date) == (False, MONDAY)
    apple = await db.scalar(select(Ticker).where(Ticker.symbol == "AAPL"))
    assert apple is not None
    assert (apple.first_seen, apple.last_seen, apple.type) == (MONDAY, NEXT_MONDAY, "common")


@pytest.mark.integration
async def test_a_relisted_symbol_gets_a_new_row(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("A")), MONDAY)
    await sync_tickers(db, plan_universe([]), date(2026, 9, 29))
    await sync_tickers(db, plan_universe(listed("A")), NEXT_MONDAY)

    rows = (await db.scalars(select(Ticker).where(Ticker.symbol == "A").order_by(Ticker.id))).all()
    assert [(r.active, r.first_seen) for r in rows] == [(False, MONDAY), (True, NEXT_MONDAY)]


@pytest.mark.integration
async def test_refuses_to_mass_delist_on_a_truncated_directory(db: AsyncSession) -> None:
    many = [
        ListedSecurity(f"T{i:03d}", f"Test Company {i} Common Stock", "NYSE", False, False)
        for i in range(200)
    ]
    await sync_tickers(db, plan_universe(many), MONDAY)

    with pytest.raises(UniverseSanityError, match="Refusing to delist"):
        await sync_tickers(db, plan_universe(many[:50]), NEXT_MONDAY)

    assert await db.scalar(select(Ticker).where(Ticker.symbol == "T199", Ticker.active))


@pytest.mark.integration
async def test_build_universe_fills_sec_reference_and_shares(db: AsyncSession) -> None:
    fundamentals = FakeFundamentals(
        identifiers=[
            CompanyIdentifier("AAPL", "0000320193", "Apple Inc.", "Nasdaq"),
            CompanyIdentifier("GOOGL", "0001652044", "Alphabet Inc.", "Nasdaq"),
            CompanyIdentifier("GOOG", "0001652044", "Alphabet Inc.", "Nasdaq"),
            CompanyIdentifier("TSM", "0001046179", "TSMC", "NYSE"),
        ],
        references={
            "0000320193": CompanyReference("0000320193", "Apple", "3571", "Electronic Computers"),
            "0001652044": CompanyReference(
                "0001652044", "Alphabet", "7370", "Services-Computer Programming"
            ),
        },
        shares={
            "0000320193": [
                SharesObservation(date(2025, 1, 17), date(2025, 1, 31), 15_037_874_000, "10-Q")
            ],
            "0001652044": [
                SharesObservation(date(2025, 4, 18), date(2025, 4, 25), 12_120_000_000, "10-Q")
            ],
            "0001046179": [
                SharesObservation(date(2024, 12, 31), date(2025, 4, 17), 25_930_000_000, "20-F")
            ],
        },
    )
    stats: dict[str, object] = {}

    await build_universe(
        db, FakeReference(listed("AAPL", "GOOGL", "GOOG", "TSM")), fundamentals, MONDAY, stats
    )

    tickers = {t.symbol: t for t in (await db.scalars(select(Ticker))).all()}
    assert tickers["AAPL"].cik == "0000320193"
    assert (tickers["GOOG"].sic_code, tickers["GOOG"].sic_description) == (
        "7370",
        "Services-Computer Programming",
    )
    assert tickers["AAPL"].reference_refreshed_at is not None
    shares = (await db.scalars(select(SharesOutstanding))).all()
    assert {tickers_by_id(tickers)[s.ticker_id] for s in shares} == {"AAPL", "GOOGL", "GOOG"}
    assert "0001046179" not in fundamentals.share_calls  # ADR share counts aren't ADS counts
    assert fundamentals.share_calls.count("0001652044") == 1  # one request per company
    assert stats["with_cik"] == 4
    assert stats["sec_errors"] == 0


def tickers_by_id(tickers: dict[str, Ticker]) -> dict[int, str]:
    return {t.id: s for s, t in tickers.items()}


@pytest.mark.integration
async def test_build_universe_without_sec_still_syncs(db: AsyncSession) -> None:
    stats: dict[str, object] = {}
    await build_universe(db, FakeReference(listed("AAPL")), None, MONDAY, stats)
    assert stats["sec"] == "skipped: SEC_USER_AGENT is not set"
    assert stats["added"] == 17


def test_market_cap_scales_share_counts_by_later_splits() -> None:
    assert market_cap(50.0, 100) == 5_000
    assert market_cap(50.0, 100, [4.0]) == 20_000
    assert market_cap(50.0, 100, [2.0, 3.0]) == 30_000


@pytest.mark.integration
async def test_latest_market_caps_are_point_in_time(db: AsyncSession) -> None:
    await sync_tickers(db, plan_universe(listed("AAPL", "A", "TSM")), MONDAY)
    ids = {t.symbol: t.id for t in (await db.scalars(select(Ticker))).all()}
    day = date(2024, 6, 14)
    for symbol in ("AAPL", "A", "TSM"):
        db.add(
            DailyBar(
                ticker_id=ids[symbol],
                date=day,
                open=50,
                high=51,
                low=49,
                close=50,
                volume=1_000,
                source="test",
            )
        )
    # AAPL: 100 shares as of March, then a 4-for-1 split in June → 400 shares now.
    db.add(
        SharesOutstanding(
            ticker_id=ids["AAPL"],
            as_of_date=date(2024, 3, 31),
            filed_date=date(2024, 5, 3),
            shares=100,
            source="test",
        )
    )
    db.add(
        CorporateAction(
            ticker_id=ids["AAPL"], ex_date=date(2024, 6, 10), kind="split", value=4.0, source="test"
        )
    )
    # A: the only share count was filed after the latest bar, so it isn't known yet.
    db.add(
        SharesOutstanding(
            ticker_id=ids["A"],
            as_of_date=date(2024, 6, 1),
            filed_date=date(2024, 6, 20),
            shares=300,
            source="test",
        )
    )
    await db.commit()

    updated = await update_latest_market_caps(db, since=date(2024, 6, 1))

    assert updated == 1
    caps = {t.symbol: t.market_cap for t in (await db.scalars(select(Ticker))).all()}
    assert caps["AAPL"] == 20_000
    assert caps["A"] is None
    assert caps["TSM"] is None  # ADR: no reliable share count

from collections.abc import Sequence
from datetime import date, timedelta

import polars as pl
import pytest

from app.groups.classification import (
    HEALTH,
    MAJOR_GROUPS,
    SECTOR_ETFS,
    TECH,
    UNCLASSIFIED,
    TickerSic,
    assign_groups,
    sector_for,
)
from app.groups.industry_rank import add_rank_trend, rank_groups, sector_rotation

D = date(2026, 10, 2)


@pytest.mark.parametrize(
    ("sic", "sector"),
    [
        ("2834", "Health Care"),  # pharmaceutical preparations
        ("3674", "Information Technology"),  # semiconductors
        ("7372", "Information Technology"),  # prepackaged software
        ("6798", "Real Estate"),  # REITs
        ("6022", "Financials"),  # state commercial banks
        ("6770", "Financials"),  # blank checks (SPACs)
        ("1311", "Energy"),  # crude petroleum & natural gas
        ("2911", "Energy"),  # petroleum refining
        ("4911", "Utilities"),  # electric services
        ("4953", "Industrials"),  # refuse systems
        ("4922", "Energy"),  # natural gas transmission
        ("2080", "Consumer Staples"),  # beverages
        ("2844", "Consumer Staples"),  # perfumes, cosmetics
        ("5331", "Consumer Staples"),  # variety stores
        ("5961", "Consumer Discretionary"),  # catalog & mail-order (e-commerce)
        ("3711", "Consumer Discretionary"),  # motor vehicles
        ("3721", "Industrials"),  # aircraft
        ("3841", "Health Care"),  # surgical & medical instruments
        ("7311", "Communication Services"),  # advertising agencies
        ("4813", "Communication Services"),  # telephone communications
        ("1531", "Consumer Discretionary"),  # operative builders
        ("8731", "Health Care"),  # commercial physical & biological research
        ("9995", "Unclassified"),  # non-operating establishments
        (None, "Unclassified"),
        ("", "Unclassified"),
    ],
)
def test_sectors(sic: str | None, sector: str) -> None:
    assert sector_for(sic) == sector


def test_every_major_group_maps_to_a_known_sector() -> None:
    sectors = set(SECTOR_ETFS.values()) | {UNCLASSIFIED}
    assert {sector for _, sector in MAJOR_GROUPS.values()} <= sectors
    assert len(SECTOR_ETFS) == 11


def test_big_codes_become_groups_and_small_ones_roll_up() -> None:
    tickers = (
        [TickerSic(i, "7372", "Services-Prepackaged Software") for i in range(1, 4)]
        + [TickerSic(10, "7374", "Services-Computer Processing & Data Preparation")]
        + [TickerSic(11, "7389", "Services-Business Services, NEC")]
        + [
            TickerSic(20, "2836", "Biological Products"),
            TickerSic(21, "2836", "Biological Products"),
        ]
        + [TickerSic(30, None, None), TickerSic(31, "abc", None)]
    )
    groups, membership = assign_groups(tickers, min_members=2)

    assert membership[1] == membership[2] == "SIC7372"
    assert groups["SIC7372"].name == "Services-Prepackaged Software"
    assert groups["SIC7372"].sector == TECH
    assert membership[10] == membership[11] == "SIC73"
    assert groups["SIC73"].name == "Business Services (other)"  # 7372 split out of 73
    assert groups["SIC2836"].sector == HEALTH
    assert 30 not in membership
    assert 31 not in membership
    assert groups["SIC73"].sic_level == 2


def members_frame(
    rows: Sequence[tuple[int, int | None, float, float, bool, bool]], day: date = D
) -> pl.DataFrame:
    return pl.DataFrame(
        [(g, day, rs, r3, r6, tt, hi) for g, rs, r3, r6, tt, hi in rows],
        schema={
            "group_id": pl.Int32,
            "date": pl.Date,
            "rs_rating": pl.Int16,
            "return_3m": pl.Float64,
            "return_6m": pl.Float64,
            "tt_pass": pl.Boolean,
            "at_high": pl.Boolean,
        },
        orient="row",
    )


def test_groups_rank_by_median_rs_and_returns() -> None:
    rows = (
        [(1, rs, 0.30, 0.50, True, True) for rs in (90, 85, 80)]  # strong
        + [(2, rs, 0.05, 0.10, False, False) for rs in (60, 55, 50)]  # middling
        + [(3, rs, -0.10, -0.20, False, False) for rs in (20, 15, 10)]  # weak
        + [(4, 99, 1.0, 1.0, True, True), (4, 99, 1.0, 1.0, True, True)]  # too small to rank
    )
    ranked = {r["group_id"]: r for r in rank_groups(members_frame(rows)).iter_rows(named=True)}

    assert set(ranked) == {1, 2, 3}
    assert [ranked[g]["rank"] for g in (1, 2, 3)] == [1, 2, 3]
    assert ranked[1]["score"] == pytest.approx(1.0)
    assert ranked[3]["score"] == pytest.approx(0.0)
    assert (ranked[1]["median_rs"], ranked[1]["tt_passing"], ranked[1]["new_highs"]) == (85, 3, 3)
    assert ranked[2]["return_3m"] == pytest.approx(0.05)


def test_rank_trend_compares_with_20_sessions_earlier() -> None:
    days = [D + timedelta(days=i) for i in range(21)]
    rows = []
    for i, d in enumerate(days):
        rows.append({"group_id": 1, "date": d, "rank": 5 if i == 0 else 2})
        rows.append({"group_id": 2, "date": d, "rank": 1 if i == 0 else 4})
    history = pl.DataFrame(rows, schema={"group_id": pl.Int32, "date": pl.Date, "rank": pl.Int16})
    latest = add_rank_trend(history).filter(pl.col("date") == days[-1])
    change = dict(latest.select("group_id", "rank_change_4w").iter_rows())
    assert change == {1: 3, 2: -3}  # 5 → 2 improved by 3; 1 → 4 fell by 3
    assert (
        add_rank_trend(history).filter(pl.col("date") == days[0])["rank_change_4w"].is_null().all()
    )


def test_sector_rotation_ranks_by_rs() -> None:
    etfs = pl.DataFrame(
        {"symbol": ["XLK", "XLE", "XLU"], "date": [D] * 3, "rs_raw": [0.25, -0.05, 0.10]}
    )
    ranks = dict(sector_rotation(etfs).select("symbol", "rank").iter_rows())
    assert ranks == {"XLK": 1, "XLU": 2, "XLE": 3}

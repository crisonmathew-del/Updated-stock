from datetime import date

import polars as pl
import pytest

from app.market.breadth import breadth_counts, finalize_breadth

D1, D2 = date(2026, 9, 30), date(2026, 10, 1)


def stock(
    tid: int,
    closes: tuple[float, float],
    *,
    sma50: float | None,
    sma200: float | None,
    high_52w: float = 999,
    low_52w: float = 1,
    history: int = 300,
    high: float | None = None,
    low: float | None = None,
) -> list[dict[str, object]]:
    rows = []
    for d, c in zip((D1, D2), closes, strict=True):
        rows.append(
            {
                "ticker_id": tid,
                "date": d,
                "close": c,
                "high": high or c,
                "low": low or c,
                "sma50": sma50,
                "sma200": sma200,
                "high_52w": high_52w,
                "low_52w": low_52w,
                "history_sessions": history,
            }
        )
    return rows


def sample() -> pl.DataFrame:
    rows = (
        stock(1, (10, 11), sma50=10.5, sma200=9, high=12, high_52w=12)  # up, new high
        + stock(2, (20, 19), sma50=19.5, sma200=21, low=18, low_52w=18)  # down, new low
        + stock(3, (30, 30), sma50=None, sma200=None)  # unchanged, no averages yet
        + stock(
            4, (5, 6), sma50=5, sma200=None, high=6, high_52w=6, history=100
        )  # IPO: no new high
    )
    return pl.DataFrame(rows).sort("ticker_id", "date")


def test_counts_and_percentages_for_a_day() -> None:
    day = finalize_breadth(breadth_counts(sample())).filter(pl.col("date") == D2).row(0, named=True)
    assert day["members"] == 4
    assert (day["with_50"], day["above_50"]) == (3, 2)  # 11 > 10.5, 19 < 19.5, 6 > 5
    assert day["pct_above_50"] == pytest.approx(200 / 3)
    assert (day["with_200"], day["above_200"]) == (2, 1)
    assert (day["new_highs"], day["new_lows"], day["net_new_highs"]) == (1, 1, 0)
    assert (day["advancers"], day["decliners"]) == (2, 1)


def test_chunks_add_up_and_the_ad_line_continues() -> None:
    df = sample()
    chunked = pl.concat(
        [
            breadth_counts(df.filter(pl.col("ticker_id") <= 2)),
            breadth_counts(df.filter(pl.col("ticker_id") > 2)),
        ]
    )
    whole = finalize_breadth(breadth_counts(df), ad_line_start=100)
    split = finalize_breadth(chunked, ad_line_start=100)
    assert whole.equals(split)
    assert split.get_column("ad_line").to_list() == [100, 101]  # D1 has no prior close; D2 +2-1


def test_weak_days_go_negative() -> None:
    rows = (
        stock(1, (20, 19), sma50=25, sma200=30, low=18, low_52w=18)  # down, new low
        + stock(2, (30, 29), sma50=35, sma200=40, low=28, low_52w=28)  # down, new low
        + stock(3, (10, 11), sma50=12, sma200=15)  # up
    )
    day = finalize_breadth(breadth_counts(pl.DataFrame(rows).sort("ticker_id", "date")), 50)
    last = day.filter(pl.col("date") == D2).row(0, named=True)
    assert (last["new_highs"], last["new_lows"], last["net_new_highs"]) == (0, 2, -2)
    assert last["ad_line"] == 49  # 1 advancer, 2 decliners

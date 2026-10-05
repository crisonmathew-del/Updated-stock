"""Indicator maths against hand-calculated values (working shown in comments), plus
property-based tests for edge cases and lookahead."""

import math
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.indicators.atr import add_atr, add_true_range
from app.indicators.compute import INDICATOR_COLUMNS, compute_indicators
from app.indicators.moving_averages import add_ema, add_slope, add_sma
from app.indicators.ranges import add_52_week_range
from app.indicators.relative_strength import add_rs_line, add_rs_raw, rs_ratings
from app.indicators.stage import add_stage
from app.indicators.volatility import add_bollinger_width
from app.indicators.volume import add_up_down_volume_ratio, add_volume_averages

START = date(2024, 1, 1)

# Six hand-checkable bars.
HIGHS = [11, 13, 12.5, 16, 15.5, 18.5]
LOWS = [9, 10, 10.5, 11, 13, 14]
CLOSES = [10, 12, 11, 15, 14, 18]
VOLUMES = [100, 200, 150, 300, 120, 400]


def frame(
    closes: Sequence[float],
    *,
    ticker: int = 1,
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    volumes: Sequence[int] | None = None,
) -> pl.DataFrame:
    n = len(closes)
    return pl.DataFrame(
        {
            "ticker_id": [ticker] * n,
            "date": [START + timedelta(days=i) for i in range(n)],
            "open": list(closes),
            "high": list(highs or closes),
            "low": list(lows or closes),
            "close": list(closes),
            "volume": list(volumes or [1_000] * n),
        },
        schema={
            "ticker_id": pl.Int32,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
        },
    )


def six_bars() -> pl.DataFrame:
    return frame(CLOSES, highs=HIGHS, lows=LOWS, volumes=VOLUMES)


def col(df: pl.DataFrame, name: str) -> list[Any]:
    return df.get_column(name).to_list()


def approx(values: list[float | None]) -> list[object]:
    return [None if v is None else pytest.approx(v, rel=1e-9) for v in values]


# --- Moving averages ------------------------------------------------------------------------


def test_sma() -> None:
    # (10+12+11)/3 = 11, (12+11+15)/3 = 12.667, (11+15+14)/3 = 13.333, (15+14+18)/3 = 15.667
    df = add_sma(six_bars(), 3)
    assert col(df, "sma3") == approx([None, None, 11, 38 / 3, 40 / 3, 47 / 3])


def test_ema_is_seeded_with_the_sma() -> None:
    # alpha = 2/(3+1) = 0.5. Seed = SMA(3) = 11; then 0.5·15 + 0.5·11 = 13;
    # 0.5·14 + 0.5·13 = 13.5; 0.5·18 + 0.5·13.5 = 15.75
    df = add_ema(six_bars(), 3)
    assert col(df, "ema3") == approx([None, None, 11, 13, 13.5, 15.75])


def test_slope_is_fractional_change() -> None:
    df = add_slope(six_bars(), "close", 2, "slope")
    assert col(df, "slope") == approx([None, None, 0.1, 15 / 12 - 1, 14 / 11 - 1, 18 / 15 - 1])


# --- ATR --------------------------------------------------------------------------------------


def test_true_range_uses_prior_close() -> None:
    # day0 H-L = 2; day1 max(3, |13-10|, |10-10|) = 3; day2 max(2, |12.5-12|, |10.5-12|) = 2;
    # day3 max(5, |16-11|, |11-11|) = 5; day4 max(2.5, |15.5-15|, |13-15|) = 2.5;
    # day5 max(4.5, |18.5-14|, |14-14|) = 4.5
    df = add_true_range(six_bars())
    assert col(df, "true_range") == approx([2, 3, 2, 5, 2.5, 4.5])


def test_wilder_atr() -> None:
    # Seed = (2+3+2)/3 = 7/3; then (prev·2 + TR)/3: (14/3 + 5)/3 = 29/9;
    # (58/9 + 2.5)/3 = 80.5/27; (161/27 + 4.5)/3 = 282.5/81
    df = add_atr(six_bars(), 3)
    assert col(df, "atr3") == approx([None, None, 7 / 3, 29 / 9, 80.5 / 27, 282.5 / 81])


# --- Volatility, ranges, volume ---------------------------------------------------------------


def test_bollinger_width_uses_population_std() -> None:
    # Window 10, 12, 11: mean 11, variance (1+1+0)/3 = 2/3, width = 2·2·sqrt(2/3)/11
    df = add_bollinger_width(six_bars(), n=3)
    assert col(df, "bb_width")[2] == pytest.approx(4 * math.sqrt(2 / 3) / 11)


def test_52_week_range_and_history_length() -> None:
    df = add_52_week_range(six_bars(), window=3)
    assert col(df, "high_52w") == [11, 13, 13, 16, 16, 18.5]
    assert col(df, "low_52w") == [9, 9, 9, 10, 10.5, 11]
    assert col(df, "close_high_52w") == [10, 12, 12, 15, 15, 18]
    assert col(df, "history_sessions") == [1, 2, 3, 4, 5, 6]


def test_volume_average_excludes_today() -> None:
    # Day 3 baseline = mean(100, 200, 150) = 150, so its 300 is 2.0x average.
    df = add_volume_averages(six_bars(), n=3, min_samples=3)
    assert col(df, "avg_volume_3") == approx([None, None, None, 150, 650 / 3, 190])
    assert col(df, "volume_ratio")[3] == pytest.approx(2.0)
    # Dollar volume baseline on day 3: (10·100 + 12·200 + 11·150)/3 = 5050/3
    assert col(df, "avg_dollar_volume_3")[3] == pytest.approx(5050 / 3)


def test_up_down_volume_ratio() -> None:
    # Up days: 1 (200), 3 (300), 5 (400). Down days: 2 (150), 4 (120). Day 0 has no prior close.
    # Windows of 3: day2 200/150; day3 (200+300)/150; day4 300/(150+120); day5 (300+400)/120
    df = add_up_down_volume_ratio(six_bars(), n=3)
    assert col(df, "up_down_volume_3") == approx(
        [None, None, 200 / 150, 500 / 150, 300 / 270, 700 / 120]
    )


# --- Relative strength ------------------------------------------------------------------------


def test_rs_raw_rescales_weights_for_short_histories() -> None:
    closes = [100 * 1.001**i for i in range(200)]
    df = add_rs_raw(frame(closes))
    roc63, roc126 = col(df, "roc_63"), col(df, "roc_126")
    raw = col(df, "rs_raw")
    assert raw[62] is None  # fewer than 63 sessions back
    assert raw[63] == pytest.approx(roc63[63])  # only the 63-day period: weight 0.4/0.4
    assert raw[150] == pytest.approx((0.4 * roc63[150] + 0.2 * roc126[150]) / 0.6)


def test_rs_rating_percentiles_ties_and_minimum_count() -> None:
    day, other = date(2025, 1, 2), date(2025, 1, 3)
    raw = pl.DataFrame(
        {
            "ticker_id": [1, 2, 3, 4, 5, 1, 2, 3, 9],
            "date": [day] * 5 + [other] * 3 + [date(2025, 1, 6)],
            "rs_raw": [0.10, 0.20, 0.30, 0.40, 0.50, 0.5, 0.5, 0.1, 0.3],
        }
    )
    ratings = {
        (r["ticker_id"], r["date"]): r["rs_rating"] for r in rs_ratings(raw).iter_rows(named=True)
    }
    # Five stocks: percentiles 0, .25, .5, .75, 1 → 1 + floor(p·98.999) = 1, 25, 50, 75, 99
    assert [ratings[(t, day)] for t in (1, 2, 3, 4, 5)] == [1, 25, 50, 75, 99]
    # Ties share the average rank: ranks 2.5, 2.5, 1 of 3 → p = .75, .75, 0
    assert [ratings[(t, other)] for t in (1, 2, 3)] == [75, 75, 1]
    assert ratings[(9, date(2025, 1, 6))] is None  # a lone stock can't be ranked


def test_rs_line_new_high_ahead_of_price() -> None:
    # The stock peaks at 120 then holds 110 while the benchmark keeps falling, so the RS line
    # makes new highs before the price does.
    closes = [100 + i * 0.25 for i in range(81)] + [110.0] * 40
    stock = add_52_week_range(frame(closes))
    benchmark = pl.DataFrame(
        {"date": stock.get_column("date"), "close": [200 - i * 0.5 for i in range(len(closes))]}
    )
    df = add_rs_line(stock, benchmark)
    last = df.row(-1, named=True)
    assert last["rs_line"] == pytest.approx(110 / (200 - 120 * 0.5))
    assert last["rs_line_high_52w"] is True
    assert last["close"] < last["close_high_52w"]
    assert last["rs_new_high_ahead"] is True
    early = df.row(30, named=True)
    assert early["rs_new_high_ahead"] is False  # fewer than 63 sessions of RS line


# --- Stage --------------------------------------------------------------------------------------


def test_stage_rules() -> None:
    sma150 = [100, 102, 104, 104.5, 104.6, 103, 101]
    closes = [101, 103, 103, 105, 106, 104, 100]
    df = frame(closes).with_columns(pl.Series("sma150", sma150, pl.Float64))
    # Slopes over 1 bar: -, +2%, +1.96%, +0.48% (flat), +0.10% (flat), -1.53%, -1.94%
    stages = col(add_stage(df, lookback=1, flat_pct=1.0), "stage")
    assert stages == [
        None,  # no slope yet
        2,  # rising MA, close above
        3,  # rising MA, close below it
        3,  # flat after an uptrend
        3,
        1,  # falling MA but close still above
        4,  # falling MA, close below
    ]


def test_flat_from_the_start_is_stage_1() -> None:
    df = frame([50, 50.2, 50.1]).with_columns(pl.Series("sma150", [50, 50.1, 50.1], pl.Float64))
    assert col(add_stage(df, lookback=1, flat_pct=1.0), "stage") == [None, 1, 1]


# --- Properties ---------------------------------------------------------------------------------

price = st.floats(min_value=1, max_value=5_000, allow_nan=False, allow_infinity=False)


@st.composite
def bar_series(draw: st.DrawFn, min_size: int = 30, max_size: int = 320) -> pl.DataFrame:
    closes = draw(st.lists(price, min_size=min_size, max_size=max_size))
    spreads = draw(st.lists(st.floats(0, 0.1), min_size=len(closes), max_size=len(closes)))
    volumes = draw(st.lists(st.integers(0, 10**9), min_size=len(closes), max_size=len(closes)))
    return frame(
        closes,
        highs=[c * (1 + s) for c, s in zip(closes, spreads, strict=True)],
        lows=[c * (1 - s) for c, s in zip(closes, spreads, strict=True)],
        volumes=volumes,
    )


def benchmark_for(df: pl.DataFrame) -> pl.DataFrame:
    return pl.DataFrame({"date": df.get_column("date"), "close": [400.0] * df.height})


def frames_equal(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    for name in a.columns:
        for x, y in zip(a.get_column(name).to_list(), b.get_column(name).to_list(), strict=True):
            if x is None or y is None:
                if x is not y:
                    return False
            elif isinstance(x, float):
                if not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12):
                    return False
            elif x != y:
                return False
    return True


@settings(max_examples=40, deadline=None)
@given(bars=bar_series(), cut=st.integers(1, 300))
def test_no_lookahead_values_never_depend_on_later_bars(bars: pl.DataFrame, cut: int) -> None:
    cut = min(cut, bars.height)
    full = compute_indicators(bars, benchmark_for(bars)).head(cut)
    truncated = compute_indicators(bars.head(cut), benchmark_for(bars.head(cut)))
    assert frames_equal(full, truncated)


@settings(max_examples=25, deadline=None)
@given(a=bar_series(max_size=120), b=bar_series(max_size=120))
def test_tickers_never_mix(a: pl.DataFrame, b: pl.DataFrame) -> None:
    b = b.with_columns(pl.lit(2, pl.Int32).alias("ticker_id"))
    together = compute_indicators(
        pl.concat([a, b]), benchmark_for(pl.concat([a, b]).unique("date"))
    )
    alone = compute_indicators(b, benchmark_for(b))
    assert frames_equal(together.filter(pl.col("ticker_id") == 2), alone)


@settings(max_examples=40, deadline=None)
@given(bars=bar_series())
def test_invariants(bars: pl.DataFrame) -> None:
    df = compute_indicators(bars, benchmark_for(bars))
    floats = [name for name, dtype in df.schema.items() if dtype == pl.Float64]
    assert df.select(pl.any_horizontal(pl.col(floats).is_nan()).any()).item() is False
    for row in df.iter_rows(named=True):
        assert row["low_52w"] <= row["close"] <= row["high_52w"] + 1e-9
        if row["atr14"] is not None:
            assert row["atr14"] >= 0
        if row["bb_width"] is not None:
            assert row["bb_width"] >= 0
        if row["stage"] is not None:
            assert row["stage"] in (1, 2, 3, 4)
    assert set(INDICATOR_COLUMNS) <= set(df.columns)


@given(level=price, n=st.integers(2, 40))
def test_constant_prices_give_constant_averages_and_zero_range(level: float, n: int) -> None:
    df = frame([level] * (n + 5))
    df = add_atr(add_ema(add_sma(df, n), n), n)
    assert col(df, f"sma{n}")[-1] == pytest.approx(level)
    assert col(df, f"ema{n}")[-1] == pytest.approx(level)
    assert col(df, f"atr{n}")[-1] == pytest.approx(0)

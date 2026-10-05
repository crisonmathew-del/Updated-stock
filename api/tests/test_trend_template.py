from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from app.scoring.trend_template import CHECKS, add_trend_template, explain_trend_template
from app.settings.schema import AppSettings

SETTINGS = AppSettings()  # spec defaults: RS ≥ 70, ≥30% above low, within 25% of high, 21 sessions
LEADER = {
    "close": 100.0,
    "sma50": 95.0,
    "sma150": 90.0,
    "low_52w": 70.0,
    "high_52w": 110.0,
    "rs_rating": 85,
}


def history(
    last: dict[str, Any], sma200_start: float = 80.0, sma200_end: float = 85.0
) -> pl.DataFrame:
    """25 sessions of one ticker; the 200-day SMA moves linearly, the last row gets `last`."""
    n = 25
    rows = []
    for i in range(n):
        row = {
            **LEADER,
            "ticker_id": 1,
            "date": date(2026, 1, 1) + timedelta(days=i),
            "sma200": sma200_start + (sma200_end - sma200_start) * i / (n - 1),
        }
        if i == n - 1:
            row.update(last)
        rows.append(row)
    return pl.DataFrame(rows).with_columns(pl.col("rs_rating").cast(pl.Int16))


def last_row(df: pl.DataFrame) -> dict[str, Any]:
    return add_trend_template(df, SETTINGS).row(-1, named=True)


def failing(row: dict[str, Any]) -> set[str]:
    return {key for key, _ in CHECKS if not row[f"tt_{key}"]}


def test_a_leader_passes_all_eight() -> None:
    row = last_row(history({}))
    assert row["tt_pass"] is True
    assert row["tt_passed"] == 8


@pytest.mark.parametrize(
    ("change", "expected_failures"),
    [
        ({"close": 89.0}, {"above_150_200", "above_50", "above_52w_low"}),
        ({"sma150": 84.0}, {"150_above_200"}),
        ({"sma50": 86.0, "sma150": 87.0}, {"50_above_150_200"}),
        ({"close": 94.0}, {"above_50"}),
        ({"low_52w": 80.0}, {"above_52w_low"}),  # 100 < 80 × 1.30
        ({"high_52w": 140.0}, {"near_52w_high"}),  # 100 < 140 × 0.75
        ({"rs_rating": 69}, {"rs_rating"}),
        ({"rs_rating": None}, {"rs_rating"}),
    ],
)
def test_each_check_fails_on_its_own(change: dict[str, Any], expected_failures: set[str]) -> None:
    row = last_row(history(change))
    assert failing(row) == expected_failures
    assert row["tt_pass"] is False


def test_a_falling_200_day_fails_the_trend_check() -> None:
    row = last_row(history({}, sma200_start=86.0, sma200_end=85.0))
    assert failing(row) == {"200_rising"}


def test_short_histories_fail_rather_than_pass() -> None:
    df = history({}).with_columns(pl.lit(None, pl.Float64).alias("sma200"))
    row = last_row(df)
    assert {"above_150_200", "150_above_200", "200_rising", "50_above_150_200"} <= failing(row)


def test_explanations_show_the_numbers() -> None:
    row = last_row(history({"close": 94.0}))
    checks = {c.key: c for c in explain_trend_template(row, SETTINGS)}
    assert len(checks) == 8
    assert checks["above_50"].passed is False
    assert checks["above_50"].detail == "Close 94.00 vs 50-day 95.00"
    assert checks["above_52w_low"].detail == "+34.3% above the 52-week low 70.00 (needs +30%)"
    assert (
        checks["near_52w_high"].detail == "-14.5% from the 52-week high 110.00 (needs within 25%)"
    )
    assert (
        checks["200_rising"].detail == "200-day 85.00 vs 80.62 21 sessions ago (+5.4%)"
    )  # 21 rows back: 80 + 5·3/24
    assert checks["rs_rating"].detail == "RS Rating 85 (needs 70)"


def test_thresholds_come_from_settings() -> None:
    strict = AppSettings(rs_rating_min=90)
    row = add_trend_template(history({}), strict).row(-1, named=True)
    assert row["tt_rs_rating"] is False

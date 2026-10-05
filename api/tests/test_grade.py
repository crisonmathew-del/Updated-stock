"""Fundamentals Grade: hand-worked examples for each path and component, point-in-time
behaviour (lookahead guard), splits and coverage."""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.fundamentals.grade import (
    GradeResult,
    InsiderTrade,
    Split,
    StatementRow,
    grade_fundamentals,
)
from app.settings.schema import DEFAULTS, AppSettings

M = 1e6


def quarter(
    end: date,
    eps: float | None,
    revenue: float | None,
    net_income: float | None = None,
    operating_income: float | None = None,
    equity: float | None = None,
    *,
    reported: date | None = None,
    label: tuple[int, str] | None = None,
) -> StatementRow:
    return StatementRow(
        period_end=end,
        reported_date=reported or end + timedelta(days=30),
        fiscal_year=label[0] if label else None,
        fiscal_period=label[1] if label else None,
        eps=eps,
        revenue=revenue,
        net_income=net_income,
        operating_income=operating_income,
        equity=equity,
    )


# A growing company: EPS +25% → +30% → +40%, sales +20% → +23.8% → +27.3%.
ACME_Q = [
    quarter(date(2022, 3, 31), 0.40, 100 * M, 4.0 * M, 17.0 * M, 90 * M, label=(2022, "Q1")),
    quarter(date(2022, 6, 30), 0.42, 105 * M, 4.2 * M, 18.0 * M, 95 * M, label=(2022, "Q2")),
    quarter(date(2022, 9, 30), 0.45, 110 * M, 4.5 * M, 19.8 * M, 100 * M, label=(2022, "Q3")),
    quarter(date(2022, 12, 31), 0.50, 120 * M, 5.0 * M, 22.0 * M, 105 * M, label=(2022, "Q4")),
    quarter(date(2023, 3, 31), 0.50, 120 * M, 5.0 * M, 22.8 * M, 110 * M, label=(2023, "Q1")),
    quarter(date(2023, 6, 30), 0.546, 130 * M, 5.46 * M, 25.0 * M, 115 * M, label=(2023, "Q2")),
    quarter(date(2023, 9, 30), 0.63, 140 * M, 6.3 * M, 28.0 * M, 120 * M, label=(2023, "Q3")),
]
ACME_A = [
    StatementRow(date(2019, 12, 31), date(2020, 2, 15), 2019, "FY", eps=0.70),
    StatementRow(date(2020, 12, 31), date(2021, 2, 15), 2020, "FY", eps=0.90),
    StatementRow(date(2021, 12, 31), date(2022, 2, 15), 2021, "FY", eps=1.20),
    StatementRow(date(2022, 12, 31), date(2023, 2, 15), 2022, "FY", eps=1.77),
]
# Annual EPS growth: 0.90/0.70 = +28.571%, 1.20/0.90 = +33.333%, 1.77/1.20 = +47.5%;
# average +36.468%; points = 20 × (0.5 + 0.5 × (36.468 - 25) / 15) = 17.6455.
ANNUAL_POINTS = 20 * (0.5 + 0.5 * ((28.571428571 + 33.333333333 + 47.5) / 3 - 25) / 15)
AS_OF = date(2023, 11, 15)


def directors_buying(*days: date) -> list[InsiderTrade]:
    return [
        InsiderTrade(
            day, day + timedelta(days=2), f"000000000{i}", f"Director {i}", "P", True, False
        )
        for i, day in enumerate(days)
    ]


def components(result: GradeResult) -> dict[str, tuple[float, str]]:
    return {c.key: (round(c.points, 4), c.status) for c in result.components}


def test_growth_company_on_the_eps_path() -> None:
    result = grade_fundamentals(
        ACME_Q,
        ACME_A,
        as_of=AS_OF,
        settings=DEFAULTS,
        up_down_volume=1.35,
        insider_trades=directors_buying(date(2023, 11, 1), date(2023, 11, 8)),
    )
    assert (result.path, result.basis, result.coverage_pct) == ("eps", "quarterly", 100.0)
    assert components(result) == {
        "eps_growth": (25.0, "pass"),  # Q3: 0.63 vs 0.45 = +40% (full credit at 40%)
        "eps_acceleration": (10.0, "pass"),  # +25% → +30% → +40%
        "sales_growth": (15.0, "pass"),  # +27.3%, faster than +23.8%
        "annual_eps_growth": (round(ANNUAL_POINTS, 4), "partial"),
        "roe": (10.0, "pass"),  # 21.76M ÷ avg(120M, 100M) = 19.8%
        "margins": (10.0, "pass"),  # operating 20.0% vs 18.0%, net 4.5% vs 4.1%
        "accumulation": (10.0, "pass"),
        "insider_bonus": (5.0, "pass"),
    }
    # 97.6455 earned + 5 bonus, capped at 100.
    assert result.score == 100.0
    assert result.grade == "A"
    details = {c.key: c.detail for c in result.components}
    assert details["eps_growth"] == (
        "Q3 FY2023 EPS $0.63 vs $0.45 a year earlier: +40.0% (needs 25%, full credit at 40%)."
    )
    assert details["roe"] == (
        "Trailing 4 quarters' net income $21.8M ÷ average equity $110.0M = 19.8% (needs 17%)."
    )
    assert "Director 0, Director 1" in details["insider_bonus"]


def test_partial_credit_adds_up_exactly() -> None:
    result = grade_fundamentals(ACME_Q, ACME_A, as_of=AS_OF, settings=DEFAULTS, up_down_volume=1.1)
    # Accumulation 1.1 is between neutral (1.0) and 1.2: half of 10. No insider cluster.
    assert components(result)["accumulation"] == (5.0, "partial")
    assert components(result)["insider_bonus"] == (0.0, "fail")
    assert result.score == pytest.approx(25 + 10 + 15 + ANNUAL_POINTS + 10 + 10 + 5, abs=0.01)
    assert result.grade == "A"


def test_grade_uses_only_what_was_reported_by_then() -> None:
    """Lookahead guard: the Q3 2023 report (filed 2023-10-30) is invisible on 2023-10-29."""
    result = grade_fundamentals(
        ACME_Q, ACME_A, as_of=date(2023, 10, 29), settings=DEFAULTS, up_down_volume=1.35
    )
    assert components(result) == {
        # Q2: 0.546 vs 0.42 = +30%: 25 × (0.5 + 0.5 × 5/15) = 16.6667
        "eps_growth": (16.6667, "partial"),
        # Only Q2 (+30%) and Q1 (+25%) have a year-earlier quarter: half.
        "eps_acceleration": (5.0, "partial"),
        "sales_growth": (15.0, "pass"),  # +23.8%, faster than +20.0%
        "annual_eps_growth": (round(ANNUAL_POINTS, 4), "partial"),
        "roe": (10.0, "pass"),  # 19.96M ÷ avg(115M, 95M) = 19.0%
        "margins": (10.0, "pass"),
        "accumulation": (10.0, "pass"),
        "insider_bonus": (0.0, "fail"),
    }
    expected = 25 / 1.5 + 5 + 15 + ANNUAL_POINTS + 10 + 10 + 10
    assert result.score == pytest.approx(expected, abs=0.01)

    # A restatement filed later changes nothing as of that day.
    restated = [*ACME_Q, quarter(date(2023, 6, 30), 0.30, 90 * M, reported=date(2023, 12, 1))]
    again = grade_fundamentals(
        restated, ACME_A, as_of=date(2023, 10, 29), settings=DEFAULTS, up_down_volume=1.35
    )
    assert again.components_json() == result.components_json()


future_rows = st.lists(
    st.builds(
        quarter,
        end=st.dates(date(2021, 1, 1), date(2024, 12, 31)),
        eps=st.floats(-5, 5, allow_nan=False),
        revenue=st.floats(1, 1e9, allow_nan=False),
        reported=st.dates(AS_OF + timedelta(days=1), date(2026, 1, 1)),
    ),
    max_size=8,
)


@given(future_rows)
@settings(max_examples=50, deadline=None)
def test_rows_reported_after_the_date_never_change_the_grade(extra: list[StatementRow]) -> None:
    base = grade_fundamentals(ACME_Q, ACME_A, as_of=AS_OF, settings=DEFAULTS, up_down_volume=1.2)
    with_future = grade_fundamentals(
        [*ACME_Q, *extra], ACME_A, as_of=AS_OF, settings=DEFAULTS, up_down_volume=1.2
    )
    assert with_future.components_json() == base.components_json()
    assert with_future.score == base.score


def test_loss_maker_takes_the_revenue_led_path() -> None:
    q = [
        quarter(date(2022, 3, 31), -0.50, 40 * M, -12 * M, -10 * M),
        quarter(date(2022, 6, 30), -0.45, 50 * M, -12.5 * M, -11 * M),
        quarter(date(2022, 9, 30), -0.40, 60 * M, -15 * M, -12 * M),
        quarter(date(2022, 12, 31), -0.35, 70 * M),
        quarter(date(2023, 3, 31), -0.30, 52 * M),
        quarter(date(2023, 6, 30), -0.25, 67.5 * M),
        quarter(date(2023, 9, 30), -0.20, 90 * M, -10.8 * M, -9 * M),
    ]
    result = grade_fundamentals(q, [], as_of=AS_OF, settings=DEFAULTS, up_down_volume=0.9)
    assert result.path == "revenue"
    assert components(result) == {
        "sales_growth": (40.0, "pass"),  # 90 vs 60 = +50% (full credit at 40%)
        "sales_acceleration": (20.0, "pass"),  # +30% → +35% → +50%
        "margins": (20.0, "pass"),  # operating -10% vs -20%, net -12% vs -25%
        "accumulation": (0.0, "fail"),  # 0.9 < 1.0
        "insider_bonus": (0.0, "fail"),
    }
    assert (result.score, result.grade) == (80.0, "A")  # exactly the A cutoff


def test_turnaround_earns_half_without_a_misleading_percentage() -> None:
    q = [
        quarter(date(2022, 9, 30), -0.05, 50 * M),
        quarter(date(2022, 12, 31), 0.05, 55 * M),
        quarter(date(2023, 3, 31), 0.08, 58 * M),
        quarter(date(2023, 6, 30), 0.09, 60 * M),
        quarter(date(2023, 9, 30), 0.10, 62 * M),
    ]
    result = grade_fundamentals(q, [], as_of=AS_OF, settings=DEFAULTS)
    eps = next(c for c in result.components if c.key == "eps_growth")
    assert (eps.points, eps.status) == (12.5, "partial")
    assert eps.detail == (
        "quarter ended 2023-09-30 EPS $0.10 vs -$0.05 a year earlier: turnaround from a loss."
    )
    assert "%" not in eps.detail


def test_twenty_f_filer_is_graded_on_fiscal_years() -> None:
    years = [
        StatementRow(
            date(2020, 12, 31),
            date(2021, 3, 30),
            2020,
            "FY",
            0.60,
            1200 * M,
            150 * M,
            equity=1000 * M,
            currency="EUR",
        ),
        StatementRow(
            date(2021, 12, 31),
            date(2022, 3, 30),
            2021,
            "FY",
            0.80,
            1600 * M,
            180 * M,
            equity=1150 * M,
            currency="EUR",
        ),
        StatementRow(
            date(2022, 12, 31),
            date(2023, 3, 30),
            2022,
            "FY",
            1.10,
            2000 * M,
            220 * M,
            equity=1300 * M,
            currency="EUR",
        ),
        StatementRow(
            date(2023, 12, 31),
            date(2024, 3, 28),
            2023,
            "FY",
            1.50,
            2600 * M,
            300 * M,
            equity=1500 * M,
            currency="EUR",
        ),
    ]
    result = grade_fundamentals([], years, as_of=date(2024, 4, 15), settings=DEFAULTS)
    assert (result.path, result.basis) == ("eps", "annual")
    eps_points = 25 * (0.5 + 0.5 * (1.5 / 1.1 * 100 - 100 - 25) / 15)  # +36.36%
    annual_avg = (0.8 / 0.6 + 1.1 / 0.8 + 1.5 / 1.1 - 3) / 3 * 100  # +35.73%
    annual_points = 20 * (0.5 + 0.5 * (annual_avg - 25) / 15)
    assert components(result) == {
        "eps_growth": (round(eps_points, 4), "partial"),
        "eps_acceleration": (0.0, "fail"),  # +33.3% → +37.5% → +36.4%
        "sales_growth": (15.0, "pass"),  # +30% after +25%
        "annual_eps_growth": (round(annual_points, 4), "partial"),
        "roe": (10.0, "pass"),  # 300M ÷ avg(1500M, 1300M) = 21.4%
        "margins": (10.0, "pass"),  # net 11.5% vs 11.0% (no operating income reported)
        "accumulation": (0.0, "no_data"),
        "insider_bonus": (0.0, "fail"),
    }
    assert result.coverage_pct == 90.0
    earned = eps_points + 15 + annual_points + 10 + 10
    assert result.score == pytest.approx(earned / 90 * 100, abs=0.01)
    eps = next(c for c in result.components if c.key == "eps_growth")
    assert eps.detail.startswith("FY2023 EPS 1.50 EUR vs 1.10 EUR a year earlier: +36.4%")


def test_eps_reported_before_a_split_is_adjusted() -> None:
    q = [
        quarter(date(2022, 9, 30), 0.90, 110 * M),  # reported 2022-10-30, before the split
        quarter(date(2023, 9, 30), 0.63, 140 * M),  # reported after the 2-for-1 split
    ]
    split = [Split(date(2023, 6, 1), 2.0)]
    result = grade_fundamentals(q, [], as_of=AS_OF, settings=DEFAULTS, splits=split)
    eps = next(c for c in result.components if c.key == "eps_growth")
    assert eps.points == 25.0  # 0.63 vs 0.90 / 2 = 0.45: +40%
    assert "vs $0.45" in eps.detail
    # Without the split it would look like a 30% decline.
    unadjusted = grade_fundamentals(q, [], as_of=AS_OF, settings=DEFAULTS)
    assert next(c for c in unadjusted.components if c.key == "eps_growth").points == 0


def test_too_little_data_gives_no_grade() -> None:
    result = grade_fundamentals([], [], as_of=AS_OF, settings=DEFAULTS, up_down_volume=1.5)
    assert result.basis == "none"
    assert (result.grade, result.score, result.coverage_pct) == (None, None, 20.0)


def test_cutoffs_come_from_settings() -> None:
    strict = AppSettings.model_validate({"grade_cutoffs": {"a": 99, "b": 95, "c": 90, "d": 85}})
    result = grade_fundamentals(ACME_Q, ACME_A, as_of=AS_OF, settings=strict, up_down_volume=1.1)
    assert result.score is not None
    assert 90 <= result.score < 95
    assert result.grade == "C"


def test_insider_cluster_needs_officers_or_directors_within_the_window() -> None:
    outside = [
        InsiderTrade(date(2023, 9, 1), date(2023, 9, 3), "01", "Early", "P", True, False),
        InsiderTrade(date(2023, 11, 10), date(2023, 11, 12), "02", "Seller", "S", True, False),
        InsiderTrade(date(2023, 11, 10), date(2023, 11, 12), "03", "Fund", "P", False, False),
        # Bought within the window but filed after the as-of date: not public yet.
        InsiderTrade(date(2023, 11, 14), date(2023, 11, 16), "04", "Late", "P", False, True),
        InsiderTrade(date(2023, 11, 1), date(2023, 11, 3), "05", "Officer", "P", False, True),
    ]
    result = grade_fundamentals(
        ACME_Q, ACME_A, as_of=AS_OF, settings=DEFAULTS, insider_trades=outside
    )
    bonus = next(c for c in result.components if c.key == "insider_bonus")
    assert bonus.points == 0
    assert bonus.detail == (
        "1 officer/director open-market buyer(s) in the last 30 days (Officer); a cluster needs 2."
    )

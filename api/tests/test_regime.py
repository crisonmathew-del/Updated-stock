"""Market regime on hand-built index paths. Highs/lows are close ± 0.3% unless stated."""

from collections.abc import Sequence
from datetime import date, timedelta

import polars as pl
import pytest

from app.market.regime import (
    MARKET,
    RegimeDay,
    RegimeState,
    combine_market,
    compute_index_regime,
)
from app.settings.schema import AppSettings

SETTINGS = AppSettings()
C, P, U = RegimeState.CORRECTION, RegimeState.UPTREND_UNDER_PRESSURE, RegimeState.CONFIRMED_UPTREND
D0 = date(2026, 3, 2)


def run(
    closes: Sequence[float],
    volumes: Sequence[int],
    *,
    sma50: float | Sequence[float],
    ema21: float | Sequence[float] = 0.0,
    breadth: float | None = None,
    settings: AppSettings = SETTINGS,
) -> list[RegimeDay]:
    n = len(closes)
    dates = [D0 + timedelta(days=i) for i in range(n)]

    def series(v: float | Sequence[float]) -> list[float]:
        return [float(v)] * n if isinstance(v, int | float) else [float(x) for x in v]

    bars = pl.DataFrame(
        {
            "date": dates,
            "high": [c * 1.003 for c in closes],
            "low": [c * 0.997 for c in closes],
            "close": [float(c) for c in closes],
            "volume": list(volumes),
            "ema21": series(ema21),
            "sma50": series(sma50),
        }
    )
    pct = dict.fromkeys(dates, breadth) if breadth is not None else None
    return compute_index_regime("SPY", bars, settings, pct)


def states(days: list[RegimeDay]) -> list[RegimeState]:
    return [d.state for d in days]


# --- Distribution days ------------------------------------------------------------------------


def test_distribution_days_need_a_real_drop_on_higher_volume_and_expire_on_a_5pct_gain() -> None:
    days = run(
        [100, 100.5, 100.2, 100.1, 99.6, 99.3, 105.3],
        [1000, 1000, 1100, 1200, 1000, 1300, 900],
        sma50=90,
    )
    # day 2: -0.30% on higher volume ✓  day 3: -0.10% (too small)  day 4: -0.50% on lower
    # volume  day 5: -0.30% on higher volume ✓
    assert [d.is_distribution_day for d in days] == [False, True, False, False, True, False]
    assert days[4].distribution_days == 2
    assert days[4].distribution_dates == [D0 + timedelta(days=2), D0 + timedelta(days=5)]
    # 105.3 is ≥ 5% above both (100.2 × 1.05 = 105.21, 99.3 × 1.05 = 104.27): both expire.
    assert days[5].distribution_days == 0


def test_distribution_days_age_out_after_25_sessions() -> None:
    closes = [100.0, 100.5, 100.2] + [100.2] * 26  # one DD on day 2, then unchanged closes
    days = run(closes, [1000, 1000, 1100] + [1000] * 26, sma50=90)
    by_index = {i + 1: d for i, d in enumerate(days)}  # results start at row 1
    assert by_index[2 + 24].distribution_days == 1  # 24 sessions later: still counted
    assert by_index[2 + 25].distribution_days == 0


# --- Rally attempts and follow-through days ----------------------------------------------------

FTD_CLOSES = [100, 99, 98, 98.5, 98.7, 98.6, 100.2, 100.5]
FTD_VOLUMES = [1000, 1000, 1000, 1000, 900, 800, 1200, 1000]


def test_follow_through_on_day_4_confirms_a_new_uptrend() -> None:
    days = run(FTD_CLOSES, FTD_VOLUMES, sma50=110, ema21=105, breadth=30)
    # Rows 1-2 fall to the low; row 3 is day 1 (+0.51%); rows 4-5 days 2-3; row 6 day 4 +1.62%
    # on higher volume (1200 > 800) → follow-through.
    assert states(days) == [C, C, C, C, C, U, U]
    assert [d.rally_day for d in days[:5]] == [None, None, 1, 2, 3]
    ftd = days[5]
    assert ftd.is_ftd
    assert ftd.last_ftd_date == D0 + timedelta(days=6)
    assert ftd.changed_from is C
    assert (
        ftd.reasons[0] == "Follow-through day: day 4 of the rally attempt, +1.62% on higher volume"
    )
    # The next day is still below the 50-day with weak breadth, but the new uptrend stands.
    assert days[6].state is U


def test_a_gain_below_the_threshold_is_not_a_follow_through() -> None:
    closes = [*FTD_CLOSES[:6], 99.6, 101.0]  # day 4 only +1.01%; day 5 +1.41% on higher volume
    days = run(closes, [*FTD_VOLUMES[:7], 1300], sma50=110, ema21=105, breadth=30)
    assert states(days)[-2:] == [C, U]
    assert days[-2].rally_day == 4
    assert days[-1].is_ftd


def test_a_big_gain_on_lower_volume_is_not_a_follow_through() -> None:
    days = run(FTD_CLOSES[:7], [*FTD_VOLUMES[:6], 700], sma50=110, breadth=30)
    assert days[-1].state is C
    assert days[-1].rally_day == 4


def test_day_3_is_too_early() -> None:
    days = run(
        [100, 99, 98, 98.5, 98.7, 100.2, 101.6],
        [1000, 1000, 1000, 1000, 900, 1000, 1100],
        sma50=110,
        breadth=30,
    )
    assert [d.rally_day for d in days[2:5]] == [1, 2, 3]
    assert not days[4].is_ftd  # +1.52% on day 3
    assert days[5].is_ftd  # +1.40% on day 4


def test_undercutting_the_low_restarts_the_count() -> None:
    days = run(
        [100, 99, 98, 98.5, 98.7, 97.0, 97.5, 97.8, 98.0, 99.5],
        [1000] * 8 + [900, 1000],
        sma50=110,
        breadth=30,
    )
    reset = days[4]  # 97.0 trades below the attempt's low of 97.71
    assert reset.rally_day is None
    assert reset.reasons[0] == "Rally attempt reset: SPY undercut the low of 97.71"
    assert [d.rally_day for d in days[5:8]] == [1, 2, 3]
    assert days[8].is_ftd  # day 4 of the new attempt


def test_a_follow_through_fails_if_the_rally_low_breaks() -> None:
    closes = [*FTD_CLOSES[:7], 97.0]  # 97.0 × 0.997 = 96.71 < rally low 97.71
    days = run(closes, [*FTD_VOLUMES[:7], 1000], sma50=110, breadth=30)
    assert states(days)[-2:] == [U, C]
    assert days[-1].reasons[0] == "Follow-through failed: the rally low was undercut"


def test_distribution_count_restarts_at_a_follow_through() -> None:
    closes = [100, 99.5, 99, 98.5, 98, 97.5, 97, 97.5, 97.6, 97.7, 99.3, 99.4]
    volumes = [1000, 1100, 1200, 1300, 1400, 1500, 1600, 900, 800, 700, 1000, 900]
    days = run(closes, volumes, sma50=110, breadth=30)
    assert days[8].distribution_days == 6  # six heavy down days in the decline
    assert days[9].is_ftd
    assert days[9].distribution_days == 0
    assert days[10].state is U


# --- Uptrend states ---------------------------------------------------------------------------


def test_below_the_21_day_ema_puts_the_uptrend_under_pressure_until_it_recovers() -> None:
    days = run(
        [100, 100.5, 101, 100.8, 101.2],
        [1000] * 5,
        sma50=90,
        ema21=[99, 99.5, 101.5, 100, 100],
    )
    assert states(days) == [U, P, U, U]


def test_distribution_days_build_pressure_then_correction() -> None:
    closes, volumes = [100.0], [1000]
    for _ in range(6):  # up 0.4% on light volume, then down 0.3% on heavier volume
        closes += [closes[-1] * 1.004, closes[-1] * 1.004 * 0.997]
        volumes += [1000, 1100]
    days = run(closes, volumes, sma50=90, ema21=95, breadth=60)
    dd_counts = [d.distribution_days for d in days]
    assert dd_counts[-1] == 6
    fifth = dd_counts.index(5)
    assert days[fifth].state is P
    assert days[-1].state is C
    assert days[-1].reasons[0] == "6 distribution days in 25 sessions"


@pytest.mark.parametrize(("breadth", "expected"), [(30.0, C), (60.0, P)])
def test_breaking_the_50_day_depends_on_breadth(breadth: float, expected: RegimeState) -> None:
    days = run([100, 100, 95], [1000] * 3, sma50=97, ema21=98, breadth=breadth)
    assert states(days) == [U, expected]


def test_thresholds_come_from_settings() -> None:
    strict = AppSettings(ftd_min_gain_pct=1.7)
    days = run(FTD_CLOSES, FTD_VOLUMES, sma50=110, breadth=30, settings=strict)
    assert not any(d.is_ftd for d in days)  # +1.62% isn't enough at 1.7%


# --- Overall market -------------------------------------------------------------------------


def test_the_market_takes_the_weakest_index() -> None:
    spy = run([100, 100.5, 101], [1000] * 3, sma50=90, ema21=95)
    qqq = [
        RegimeDay(d.date, "QQQ", s, reasons=["QQQ reason"])
        for d, s in zip(spy, [U, P], strict=True)
    ]
    market = combine_market({"SPY": spy, "QQQ": qqq})
    assert [m.index_symbol for m in market] == [MARKET, MARKET]
    assert states(market) == [U, P]
    assert market[1].changed_from is U
    assert market[1].reasons[:3] == [
        "QQQ: uptrend under pressure",
        "SPY: confirmed uptrend",
        "QQQ reason",
    ]


def test_reasons_carry_the_numbers() -> None:
    days = run(FTD_CLOSES, FTD_VOLUMES, sma50=110, ema21=105, breadth=30)
    reasons = days[5].reasons
    assert "SPY 8.9% below its 50-day SMA (100.20 vs 110.00)" in reasons
    assert "No distribution days in the last 25 sessions" in reasons
    assert "30% of stocks are above their 50-day SMA" in reasons
    assert "Last follow-through day 2026-03-08" in reasons

"""Trade plans, worked by hand with the default settings ($100,000 account, 1% risk, 25% max
position, 8% max stop, 0.1% stop buffer)."""

import pytest

from app.risk.trade_plan import build_trade_plan, down_to_cent, entry_price, up_to_cent
from app.settings.schema import DEFAULTS, AppSettings


def test_cent_rounding_is_robust_to_float_noise() -> None:
    assert up_to_cent(92.56) == 92.56  # 92.56 * 100 = 9255.999999999998 in floating point
    assert up_to_cent(92.561) == 92.57
    assert down_to_cent(88.7112) == 88.71
    assert down_to_cent(85.15) == 85.15


def test_entry_is_ten_cents_above_or_a_tenth_of_a_percent_for_high_prices() -> None:
    assert entry_price(92.46, DEFAULTS) == 92.56
    assert entry_price(250.0, DEFAULTS) == 250.25  # 250 × 1.001
    assert entry_price(100.0, DEFAULTS) == 100.10  # at the threshold: 0.1% = $0.10


def test_vcp_plan_by_hand() -> None:
    # Pivot 92.46, final contraction low 88.80, 21-day EMA 90.21, 50-day SMA 89.85.
    plan = build_trade_plan(
        pivot=92.46, logical_low=88.80, settings=DEFAULTS, ema21=90.214, sma50=89.851
    )
    assert plan.entry == 92.56
    assert plan.logical_stop == 88.71  # 88.80 × 0.999 = 88.7112 → 88.71
    assert plan.max_loss_stop == 85.15  # 92.56 × 0.92 = 85.1552 → 85.15
    assert (plan.stop, plan.stop_basis, plan.risk_too_wide) == (88.71, "logical", False)
    assert plan.risk_per_share == 3.85
    assert plan.risk_pct == 4.16  # 3.85 / 92.56
    # $1,000 risk / 3.85 = 259.7 → 259 shares; the 25% cap would allow 270.
    assert (plan.shares, plan.capped_by_position_limit) == (259, False)
    assert plan.dollar_risk == 997.15
    assert plan.position_value == 23973.04
    assert plan.position_pct == 23.97
    assert plan.buy_zone == (92.46, 97.08)
    assert (plan.target_2r, plan.target_3r) == (100.26, 104.11)
    assert plan.profit_take == (111.08, 115.70)  # 92.56 × 1.20 = 111.072 → up to 111.08
    # 2R (100.26) comes before +10% (101.82): breakeven at 100.26.
    assert (plan.breakeven_at, plan.breakeven_basis) == (100.26, "2R")
    assert (plan.trail_aggressive, plan.trail_standard) == (90.21, 89.85)
    assert plan.reward_risk == pytest.approx((111.08 - 92.56) / 3.85, abs=0.005)
    assert plan.notes == []


def test_a_wide_logical_stop_is_flagged_and_the_max_loss_stop_used() -> None:
    plan = build_trade_plan(pivot=100.0, logical_low=85.0, settings=DEFAULTS)
    # Entry 100.10; logical 85 × 0.999 = 84.915 → 84.91 is 15.2% away; max loss 92.09.
    assert (plan.stop, plan.stop_basis, plan.risk_too_wide) == (92.09, "max_loss", True)
    assert plan.risk_per_share == 8.01
    assert plan.notes[0].startswith("Risk too wide: the logical stop 84.91 is 15.2% below")


def test_tight_stops_are_capped_by_the_position_limit() -> None:
    plan = build_trade_plan(pivot=50.0, logical_low=49.5, settings=DEFAULTS)
    # Entry 50.10, stop 49.45, risk 0.65: $1,000 / 0.65 = 1,538 shares, but 25% of
    # $100,000 / 50.10 = 499 shares.
    assert (plan.stop, plan.risk_per_share) == (49.45, 0.65)
    assert (plan.shares, plan.capped_by_position_limit) == (499, True)
    assert plan.position_value == 24999.9
    assert plan.dollar_risk == 324.35
    assert "Size capped at 25% of the account (499 shares)" in plan.notes[0]


def test_breakeven_comes_from_the_gain_when_the_stop_is_wide() -> None:
    plan = build_trade_plan(pivot=100.0, logical_low=93.5, settings=DEFAULTS)
    # Entry 100.10, stop 93.40 (93.5 × 0.999 = 93.4065), risk 6.70: 2R = 113.50,
    # +10% = 110.11 comes first.
    assert (plan.breakeven_at, plan.breakeven_basis) == (110.11, "gain")


def test_plans_follow_the_account_settings() -> None:
    small = AppSettings(account_size=20_000, risk_per_trade_pct=0.5, account_currency="GBP")
    plan = build_trade_plan(pivot=92.46, logical_low=88.80, settings=small)
    assert plan.shares == 25  # £100 / 3.85 = 25.97
    assert plan.currency == "GBP"

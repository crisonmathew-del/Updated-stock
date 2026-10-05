"""Every user-tunable threshold, with the defaults from spec §14.

`AppSettings` is the single definition of keys, types, defaults, bounds and descriptions. The
`settings` table stores current values; `app.settings.store` merges them over these defaults and
validates the result, so an invalid value can never be saved.

Not to be confused with `app.core.config.Settings`, which is environment/infrastructure config.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    UNIVERSE = "universe"
    TREND = "trend"
    FUNDAMENTALS = "fundamentals"
    GROUPS = "groups"
    PATTERNS = "patterns"
    ENTRIES = "entries"
    MARKET = "market"
    RISK = "risk"
    ALERTS = "alerts"
    DATA = "data"


def _field(default: Any, category: Category, description: str, **bounds: Any) -> Any:
    return Field(
        default, description=description, json_schema_extra={"category": category}, **bounds
    )


class RegimeMultipliers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_uptrend: float = Field(1.0, ge=0, le=1)
    uptrend_under_pressure: float = Field(0.8, ge=0, le=1)
    correction: float = Field(0.5, ge=0, le=1)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # --- Universe (spec §5.3) -----------------------------------------------------------------
    min_price: float = _field(10, Category.UNIVERSE, "Minimum share price ($)", ge=0)
    min_avg_dollar_volume_50d: float = _field(
        20_000_000, Category.UNIVERSE, "Minimum 50-day average dollar volume ($)", ge=0
    )
    min_market_cap: float = _field(1_000_000_000, Category.UNIVERSE, "Minimum market cap ($)", ge=0)
    small_cap_mode: bool = _field(
        False, Category.UNIVERSE, "Use the small-cap liquidity filters below instead"
    )
    small_cap_min_price: float = _field(
        5, Category.UNIVERSE, "Small-cap mode: minimum price ($)", ge=0
    )
    small_cap_min_avg_dollar_volume_50d: float = _field(
        5_000_000,
        Category.UNIVERSE,
        "Small-cap mode: minimum 50-day average dollar volume ($)",
        ge=0,
    )
    small_cap_min_market_cap: float = _field(
        300_000_000, Category.UNIVERSE, "Small-cap mode: minimum market cap ($)", ge=0
    )
    include_adrs: bool = _field(
        True, Category.UNIVERSE, "Include American Depositary Receipts (e.g. TSM, ASML)"
    )

    # --- Trend Template & relative strength (spec §6.3, §6.4) ---------------------------------
    rs_rating_min: int = _field(70, Category.TREND, "Minimum RS Rating (1-99)", ge=1, le=99)
    pct_above_52w_low_min: float = _field(
        30, Category.TREND, "Close at least this % above the 52-week low", ge=0
    )
    pct_below_52w_high_max: float = _field(
        25, Category.TREND, "Close within this % of the 52-week high", ge=0, le=100
    )
    ma200_uptrend_lookback_days: int = _field(
        21, Category.TREND, "200-day SMA must be higher than this many sessions ago", ge=1
    )

    # --- Fundamentals (spec §6.5) -------------------------------------------------------------
    eps_growth_q_min: float = _field(25, Category.FUNDAMENTALS, "Quarterly EPS growth YoY (%)")
    sales_growth_q_min: float = _field(20, Category.FUNDAMENTALS, "Quarterly sales growth YoY (%)")
    eps_growth_annual_min: float = _field(
        25, Category.FUNDAMENTALS, "Average annual EPS growth over 3 years (%)"
    )
    roe_min: float = _field(17, Category.FUNDAMENTALS, "Return on equity (%)")

    # --- Industry groups (spec §6.6) ----------------------------------------------------------
    top_groups_preferred: int = _field(
        40, Category.GROUPS, "Prefer stocks in the top N industry groups", ge=1
    )

    # --- Patterns (spec §6.7) -----------------------------------------------------------------
    vcp_min_contractions: int = _field(2, Category.PATTERNS, "VCP: minimum contractions", ge=1)
    vcp_max_contractions: int = _field(6, Category.PATTERNS, "VCP: maximum contractions", ge=1)
    vcp_contraction_ratio_max: float = _field(
        0.7,
        Category.PATTERNS,
        "VCP: each contraction at most this × the previous depth",
        gt=0,
        le=1,
    )
    vcp_final_contraction_max_pct: float = _field(
        10, Category.PATTERNS, "VCP: final contraction depth at most (%)", gt=0
    )
    flat_base_max_depth_pct: float = _field(
        15, Category.PATTERNS, "Flat base: maximum depth (%)", gt=0
    )
    cup_max_depth_pct: float = _field(33, Category.PATTERNS, "Cup: maximum depth (%)", gt=0)
    handle_max_depth_pct: float = _field(12, Category.PATTERNS, "Handle: maximum depth (%)", gt=0)

    # --- Entry triggers (spec §6.8) -----------------------------------------------------------
    breakout_volume_min_pct_of_avg: float = _field(
        140, Category.ENTRIES, "Breakout volume at least this % of the 50-day average", gt=0
    )
    buy_zone_max_pct_above_pivot: float = _field(
        5, Category.ENTRIES, "Buy zone extends this % above the pivot", gt=0
    )
    near_pivot_pct: float = _field(3, Category.ENTRIES, "'Near pivot' means within this %", gt=0)
    earnings_gap_min_pct: float = _field(
        8, Category.ENTRIES, "Earnings gap: minimum gap up (%)", gt=0
    )
    earnings_gap_min_volume_multiple: float = _field(
        3, Category.ENTRIES, "Earnings gap: volume at least this × average", gt=0
    )
    earnings_warning_days: int = _field(
        5, Category.ENTRIES, "Warn when earnings are within this many trading days", ge=0
    )

    # --- Market regime (spec §6.1) ------------------------------------------------------------
    distribution_day_min_drop_pct: float = _field(
        0.2, Category.MARKET, "Distribution day: index down at least (%)", gt=0
    )
    ftd_min_gain_pct: float = _field(
        1.25, Category.MARKET, "Follow-through day: index up at least (%)", gt=0
    )
    regime_multipliers: RegimeMultipliers = _field(
        RegimeMultipliers(), Category.MARKET, "Score multiplier per market regime"
    )

    # --- Risk & position sizing (spec §6.11) --------------------------------------------------
    account_size: float = _field(100_000, Category.RISK, "Account size", gt=0)
    account_currency: str = _field("USD", Category.RISK, "Account currency", pattern="^[A-Z]{3}$")
    risk_per_trade_pct: float = _field(
        1.0, Category.RISK, "Risk per trade (% of account)", gt=0, le=10
    )
    max_position_pct: float = _field(
        25, Category.RISK, "Maximum position size (% of account)", gt=0, le=100
    )
    max_stop_loss_pct: float = _field(
        8, Category.RISK, "Maximum stop distance from entry (%)", gt=0, le=50
    )

    # --- Alerts (spec §7.3) -------------------------------------------------------------------
    alert_cooldown_minutes: int = _field(
        390, Category.ALERTS, "Minimum minutes between repeats of the same alert", ge=0
    )

    # --- Data (spec §5.5) ---------------------------------------------------------------------
    backfill_years: int = _field(
        10, Category.DATA, "Years of daily history to backfill", ge=2, le=30
    )

    @model_validator(mode="after")
    def _check_ranges(self) -> "AppSettings":
        if self.vcp_min_contractions > self.vcp_max_contractions:
            raise ValueError("vcp_min_contractions must not exceed vcp_max_contractions")
        return self


DEFAULTS = AppSettings()


def category_of(key: str) -> Category:
    extra = AppSettings.model_fields[key].json_schema_extra
    assert isinstance(extra, dict)
    return Category(str(extra["category"]))

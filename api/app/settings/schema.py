"""Every user-tunable threshold, with the defaults from spec §14.

`AppSettings` is the single definition of keys, types, defaults, bounds and descriptions. The
`settings` table stores current values; `app.settings.store` merges them over these defaults and
validates the result, so an invalid value can never be saved.

Not to be confused with `app.core.config.Settings`, which is environment/infrastructure config.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    UNIVERSE = "universe"
    TREND = "trend"
    FUNDAMENTALS = "fundamentals"
    GROUPS = "groups"
    PATTERNS = "patterns"
    ENTRIES = "entries"
    SCORING = "scoring"
    MARKET = "market"
    RISK = "risk"
    INTRADAY = "intraday"
    ALERTS = "alerts"
    BACKTEST = "backtest"
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


class GradeWeights(BaseModel):
    """Points per Fundamentals Grade component on the EPS path (spec §6.5). They need not sum
    to 100: the score is rescaled over the components that have data."""

    model_config = ConfigDict(extra="forbid")

    eps_growth: float = Field(25, ge=0, le=100)
    eps_acceleration: float = Field(10, ge=0, le=100)
    sales_growth: float = Field(15, ge=0, le=100)
    annual_eps_growth: float = Field(20, ge=0, le=100)
    roe: float = Field(10, ge=0, le=100)
    margins: float = Field(10, ge=0, le=100)
    accumulation: float = Field(10, ge=0, le=100)
    insider_bonus: float = Field(5, ge=0, le=20)


class RevenueGradeWeights(BaseModel):
    """Points per component on the revenue-led path (companies without positive EPS)."""

    model_config = ConfigDict(extra="forbid")

    sales_growth: float = Field(40, ge=0, le=100)
    sales_acceleration: float = Field(20, ge=0, le=100)
    margins: float = Field(20, ge=0, le=100)
    accumulation: float = Field(20, ge=0, le=100)


class GradeCutoffs(BaseModel):
    """Minimum score (0-100) for each grade; below `d` is an E."""

    model_config = ConfigDict(extra="forbid")

    a: float = Field(80, ge=0, le=100)
    b: float = Field(65, ge=0, le=100)
    c: float = Field(50, ge=0, le=100)
    d: float = Field(35, ge=0, le=100)

    @model_validator(mode="after")
    def _descending(self) -> "GradeCutoffs":
        if not self.a > self.b > self.c > self.d:
            raise ValueError("Grade cutoffs must satisfy A > B > C > D")
        return self


class SetupWeights(BaseModel):
    """Points per Setup Score component (spec §6.10). Rescaled over components with data."""

    model_config = ConfigDict(extra="forbid")

    trend: float = Field(20, ge=0, le=100)
    relative_strength: float = Field(20, ge=0, le=100)
    fundamentals: float = Field(20, ge=0, le=100)
    pattern: float = Field(20, ge=0, le=100)
    group: float = Field(10, ge=0, le=100)
    accumulation: float = Field(10, ge=0, le=100)


class SetupGradeCutoffs(BaseModel):
    """Minimum final Setup Score for each letter; below `c` is not a recommendation."""

    model_config = ConfigDict(extra="forbid")

    a_plus: float = Field(90, ge=0, le=100)
    a: float = Field(80, ge=0, le=100)
    b: float = Field(70, ge=0, le=100)
    c: float = Field(60, ge=0, le=100)

    @model_validator(mode="after")
    def _descending(self) -> "SetupGradeCutoffs":
        if not self.a_plus > self.a > self.b > self.c:
            raise ValueError("Setup grade cutoffs must satisfy A+ > A > B > C")
        return self


class RedFlagPenalties(BaseModel):
    """Points subtracted from the Setup Score per red flag (spec §6.9)."""

    model_config = ConfigDict(extra="forbid")

    extended: float = Field(10, ge=0, le=100)
    late_stage: float = Field(10, ge=0, le=100)
    climax: float = Field(10, ge=0, le=100)
    wide_and_loose: float = Field(5, ge=0, le=100)
    distribution: float = Field(5, ge=0, le=100)
    earnings_soon: float = Field(0, ge=0, le=100)


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
    stage_slope_lookback_days: int = _field(
        20,
        Category.TREND,
        "Stage analysis: measure the 150-day SMA slope over this many sessions",
        ge=1,
        le=100,
    )
    stage_flat_slope_pct: float = _field(
        1.0,
        Category.TREND,
        "Stage analysis: a 150-day SMA moving less than this % counts as flat",
        ge=0,
        le=20,
    )

    # --- Fundamentals (spec §6.5) -------------------------------------------------------------
    eps_growth_q_min: float = _field(25, Category.FUNDAMENTALS, "Quarterly EPS growth YoY (%)")
    sales_growth_q_min: float = _field(20, Category.FUNDAMENTALS, "Quarterly sales growth YoY (%)")
    eps_growth_annual_min: float = _field(
        25, Category.FUNDAMENTALS, "Average annual EPS growth over 3 years (%)"
    )
    roe_min: float = _field(17, Category.FUNDAMENTALS, "Return on equity (%)")
    eps_growth_q_strong: float = _field(
        40, Category.FUNDAMENTALS, "Quarterly EPS growth that earns full credit (%)"
    )
    sales_growth_q_strong: float = _field(
        40, Category.FUNDAMENTALS, "Revenue-led grade: sales growth that earns full credit (%)"
    )
    eps_growth_annual_strong: float = _field(
        40, Category.FUNDAMENTALS, "Annual EPS growth that earns full credit (%)"
    )
    accumulation_up_down_ratio_min: float = _field(
        1.2,
        Category.FUNDAMENTALS,
        "Accumulation: 50-day up/down volume ratio at least",
        gt=0,
    )
    insider_cluster_min_insiders: int = _field(
        2, Category.FUNDAMENTALS, "Insider cluster buy: at least this many insiders", ge=1
    )
    insider_cluster_window_days: int = _field(
        30,
        Category.FUNDAMENTALS,
        "Insider cluster buy: open-market purchases within this many days",
        ge=1,
        le=365,
    )
    grade_weights: GradeWeights = _field(
        GradeWeights(), Category.FUNDAMENTALS, "Fundamentals Grade: points per component"
    )
    revenue_grade_weights: RevenueGradeWeights = _field(
        RevenueGradeWeights(),
        Category.FUNDAMENTALS,
        "Fundamentals Grade, revenue-led path (no positive EPS): points per component",
    )
    grade_cutoffs: GradeCutoffs = _field(
        GradeCutoffs(), Category.FUNDAMENTALS, "Fundamentals Grade: minimum score for A-D"
    )
    grade_min_coverage_pct: float = _field(
        50,
        Category.FUNDAMENTALS,
        "Show a grade only when components with data carry at least this % of the points",
        ge=0,
        le=100,
    )

    # --- Industry groups (spec §6.6) ----------------------------------------------------------
    top_groups_preferred: int = _field(
        40, Category.GROUPS, "Prefer stocks in the top N industry groups", ge=1
    )
    group_min_members: int = _field(
        5, Category.GROUPS, "An industry code needs this many stocks to be its own group", ge=1
    )

    # --- Patterns (spec §6.7) -----------------------------------------------------------------
    swing_atr_multiple: float = _field(
        1.5,
        Category.PATTERNS,
        "Swing detection: a reversal of this × ATR(14) confirms a swing high or low",
        gt=0,
        le=10,
    )
    prior_uptrend_min_pct: float = _field(
        25, Category.PATTERNS, "A base must follow an advance of at least this % off a low", ge=0
    )
    prior_uptrend_lookback_days: int = _field(
        130,
        Category.PATTERNS,
        "Look for the prior advance's low within this many sessions before the base",
        ge=20,
        le=500,
    )
    dry_up_day_pct_of_avg: float = _field(
        50,
        Category.PATTERNS,
        "Volume dry-up: a day below this % of the 50-day average volume",
        gt=0,
        le=100,
    )
    base_left_side_days: int = _field(
        40,
        Category.PATTERNS,
        "A VCP or flat base starts at the highest high of at least this many prior sessions",
        ge=0,
        le=250,
    )
    late_stage_base_number: int = _field(
        4, Category.PATTERNS, "A base this far into Stage 2 (or later) is late-stage", ge=2
    )
    base_count_min_correction_pct: float = _field(
        8,
        Category.PATTERNS,
        "Base count: a pullback of at least this % (lasting the flat-base minimum) is a base",
        gt=0,
    )
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
    vcp_first_contraction_max_pct: float = _field(
        35, Category.PATTERNS, "VCP: first (deepest) contraction at most (%)", gt=0, le=100
    )
    vcp_min_weeks: float = _field(3, Category.PATTERNS, "VCP: minimum duration (weeks)", gt=0)
    vcp_max_weeks: float = _field(65, Category.PATTERNS, "VCP: maximum duration (weeks)", gt=0)
    flat_base_max_depth_pct: float = _field(
        15, Category.PATTERNS, "Flat base: maximum depth (%)", gt=0
    )
    flat_base_min_weeks: float = _field(
        5, Category.PATTERNS, "Flat base: minimum duration (weeks)", gt=0
    )
    cup_min_depth_pct: float = _field(12, Category.PATTERNS, "Cup: minimum depth (%)", ge=0)
    cup_max_depth_pct: float = _field(33, Category.PATTERNS, "Cup: maximum depth (%)", gt=0)
    cup_bear_market_max_depth_pct: float = _field(
        50,
        Category.PATTERNS,
        "Cup: maximum depth (%) when the market was in correction during the cup",
        gt=0,
        le=100,
    )
    cup_min_weeks: float = _field(
        7, Category.PATTERNS, "Cup with handle: minimum duration (weeks)", gt=0
    )
    cup_max_weeks: float = _field(
        65, Category.PATTERNS, "Cup with handle: maximum duration (weeks)", gt=0
    )
    cup_min_bottom_share_pct: float = _field(
        40,
        Category.PATTERNS,
        "Cup must be U-shaped: at least this % of its closes in its bottom third "
        "(a V with straight sides has 33%)",
        ge=0,
        le=100,
    )
    handle_min_depth_pct: float = _field(5, Category.PATTERNS, "Handle: minimum depth (%)", ge=0)
    handle_max_depth_pct: float = _field(12, Category.PATTERNS, "Handle: maximum depth (%)", gt=0)
    handle_min_days: int = _field(5, Category.PATTERNS, "Handle: minimum length (sessions)", ge=1)
    htf_min_gain_pct: float = _field(
        90, Category.PATTERNS, "High tight flag: minimum gain of the pole (%)", gt=0
    )
    htf_max_pole_weeks: float = _field(
        8, Category.PATTERNS, "High tight flag: the pole forms within (weeks)", gt=0
    )
    htf_flag_min_depth_pct: float = _field(
        10, Category.PATTERNS, "High tight flag: minimum pullback (%)", ge=0
    )
    htf_flag_max_depth_pct: float = _field(
        25, Category.PATTERNS, "High tight flag: maximum pullback (%)", gt=0
    )
    htf_flag_min_weeks: float = _field(
        3, Category.PATTERNS, "High tight flag: minimum flag length (weeks)", gt=0
    )
    htf_flag_max_weeks: float = _field(
        5, Category.PATTERNS, "High tight flag: maximum flag length (weeks)", gt=0
    )
    three_weeks_tight_pct: float = _field(
        1.5,
        Category.PATTERNS,
        "Three-weeks-tight: weekly closes within this % of each other",
        gt=0,
        le=10,
    )
    ascending_pullback_min_pct: float = _field(
        10, Category.PATTERNS, "Ascending base: each pullback at least (%)", gt=0
    )
    ascending_pullback_max_pct: float = _field(
        20, Category.PATTERNS, "Ascending base: each pullback at most (%)", gt=0
    )
    ascending_min_weeks: float = _field(
        9, Category.PATTERNS, "Ascending base: minimum duration (weeks)", gt=0
    )
    ascending_max_weeks: float = _field(
        16, Category.PATTERNS, "Ascending base: maximum duration (weeks)", gt=0
    )

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
    earnings_gap_catalyst_sessions: int = _field(
        1,
        Category.ENTRIES,
        "Earnings gap: the results filing is on the gap day or up to this many sessions before",
        ge=0,
        le=5,
    )
    pocket_pivot_lookback_days: int = _field(
        10,
        Category.ENTRIES,
        "Pocket pivot: volume must beat every down day's volume over this many sessions",
        ge=1,
    )
    pocket_pivot_ma_distance_pct: float = _field(
        3,
        Category.ENTRIES,
        "Pocket pivot: close at most this % above the 10- or 50-day SMA (or a forming base)",
        ge=0,
    )

    breakout_close_range_min_pct: float = _field(
        67,
        Category.ENTRIES,
        "Confirmed breakout: close at least this % of the way up the day's range",
        ge=0,
        le=100,
    )
    failed_breakout_sessions: int = _field(
        3,
        Category.ENTRIES,
        "A breakout fails if it closes back below the pivot within this many sessions",
        ge=0,
    )
    pullback_rs_min: int = _field(
        85, Category.ENTRIES, "Pullback entry: RS Rating at least", ge=1, le=99
    )
    pullback_breakout_within_sessions: int = _field(
        30,
        Category.ENTRIES,
        "Pullback entry: the breakout happened within this many sessions",
        ge=1,
    )
    pullback_ma_touch_pct: float = _field(
        1,
        Category.ENTRIES,
        "Pullback entry: the low comes within this % of the 10/21-day EMA or 50-day SMA",
        ge=0,
    )
    undercut_max_pct: float = _field(
        3,
        Category.ENTRIES,
        "Undercut & rally: the low undercuts a prior base low by at most this %",
        gt=0,
    )
    undercut_within_sessions: int = _field(
        5,
        Category.ENTRIES,
        "Undercut & rally: the undercut happened within this many sessions",
        ge=1,
    )

    # --- Setup Score & red flags (spec §6.9, §6.10) -------------------------------------------
    setup_weights: SetupWeights = _field(
        SetupWeights(), Category.SCORING, "Setup Score: points per component"
    )
    setup_grade_cutoffs: SetupGradeCutoffs = _field(
        SetupGradeCutoffs(), Category.SCORING, "Setup Score: minimum final score for A+ to C"
    )
    red_flag_penalties: RedFlagPenalties = _field(
        RedFlagPenalties(), Category.SCORING, "Points subtracted per red flag"
    )
    extended_above_50d_pct: float = _field(
        25, Category.SCORING, "Red flag: close more than this % above the 50-day SMA", gt=0
    )
    wide_loose_weekly_range_pct: float = _field(
        15,
        Category.SCORING,
        "Red flag (wide and loose): a week in the base with a range above this %, closing low",
        gt=0,
    )
    distribution_volume_multiple: float = _field(
        1.5,
        Category.SCORING,
        "Red flag (distribution): a down day on at least this × average volume",
        gt=0,
    )
    distribution_days_in_base: int = _field(
        3,
        Category.SCORING,
        "Red flag (distribution): this many heavy down days in the base",
        ge=1,
    )
    climax_gain_pct: float = _field(
        70, Category.SCORING, "Red flag (climax run): a gain of at least this %", gt=0
    )
    climax_max_weeks: float = _field(
        3, Category.SCORING, "Red flag (climax run): within this many weeks", gt=0
    )

    # --- Market regime (spec §6.1) ------------------------------------------------------------
    distribution_day_min_drop_pct: float = _field(
        0.2, Category.MARKET, "Distribution day: index down at least (%)", gt=0
    )
    ftd_min_gain_pct: float = _field(
        1.25, Category.MARKET, "Follow-through day: index up at least (%)", gt=0
    )
    ftd_min_day: int = _field(
        4, Category.MARKET, "Follow-through day: earliest day of the rally attempt", ge=1
    )
    ftd_failure_window_sessions: int = _field(
        10,
        Category.MARKET,
        "A follow-through fails if the rally low is undercut within this many sessions",
        ge=0,
    )
    distribution_day_lookback_sessions: int = _field(
        25, Category.MARKET, "Count distribution days over this many sessions", ge=1
    )
    distribution_day_expiry_gain_pct: float = _field(
        5, Category.MARKET, "A distribution day expires once the index closes this % above it", gt=0
    )
    regime_pressure_distribution_days: int = _field(
        5, Category.MARKET, "Uptrend under pressure at this many distribution days", ge=1
    )
    regime_correction_distribution_days: int = _field(
        6, Category.MARKET, "Correction at this many distribution days", ge=1
    )
    regime_confirmed_max_distribution_days: int = _field(
        4,
        Category.MARKET,
        "Back to confirmed uptrend at or below this many distribution days",
        ge=0,
    )
    breadth_weak_pct_above_50: float = _field(
        40,
        Category.MARKET,
        "Breadth is weak when fewer than this % of stocks are above their 50-day SMA",
        ge=0,
        le=100,
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
    entry_offset_amount: float = _field(
        0.10, Category.RISK, "Entry: this many dollars above the pivot", ge=0
    )
    entry_offset_pct: float = _field(
        0.1, Category.RISK, "Entry for high-priced stocks: this % above the pivot", ge=0
    )
    entry_offset_pct_from_price: float = _field(
        100, Category.RISK, "High-priced stocks: pivot at or above this price", gt=0
    )
    stop_buffer_pct: float = _field(
        0.1, Category.RISK, "Stop: this % below the base's logical low", ge=0, le=5
    )
    profit_take_min_pct: float = _field(
        20, Category.RISK, "Suggest taking partial profits from this % gain", gt=0
    )
    profit_take_max_pct: float = _field(
        25, Category.RISK, "Suggest taking partial profits up to this % gain", gt=0
    )
    breakeven_after_r: float = _field(
        2, Category.RISK, "Raise the stop to breakeven once up this many R", gt=0
    )
    breakeven_after_gain_pct: float = _field(
        10, Category.RISK, "Or raise it to breakeven once up this % (whichever first)", gt=0
    )

    # --- Intraday watcher and scans (spec §6.8, §7.1) -----------------------------------------
    stream_max_symbols: int = _field(
        30,
        Category.INTRADAY,
        "Most symbols the intraday watcher streams (holdings first, then setups closest to "
        "their pivot, watchlists and alert rules). Alpaca's free plan allows 30",
        ge=1,
        le=10000,
    )
    breakout_volume_strong_pct_of_avg: float = _field(
        200, Category.INTRADAY, "A breakout on at least this % of average volume is 'strong'", gt=0
    )
    partial_feed_volume_share_pct: float = _field(
        2.5,
        Category.INTRADAY,
        "With an IEX-only feed (Alpaca's free plan), the share of the market's volume it sees "
        "(%); intraday volume is scaled up by it and stays provisional until the close",
        gt=0,
        le=100,
    )
    intraday_projection_min_minutes: int = _field(
        5,
        Category.INTRADAY,
        "Minutes after the open before projected volume can confirm an intraday breakout",
        ge=0,
        le=120,
    )
    volume_curve_min_sessions: int = _field(
        20,
        Category.INTRADAY,
        "Sessions of stored minute bars needed before the learned time-of-day volume curve "
        "replaces the standard one",
        ge=1,
        le=250,
    )
    premarket_gap_min_pct: float = _field(
        4, Category.INTRADAY, "Pre-market scan: a gap of at least this % from the close", gt=0
    )
    premarket_volume_min_pct_of_avg: float = _field(
        3,
        Category.INTRADAY,
        "Pre-market scan: pre-market volume at least this % of the 50-day average",
        ge=0,
    )
    sweep_volume_ratio_min: float = _field(
        2,
        Category.INTRADAY,
        "Intraday sweep: projected volume at least this multiple of the 50-day average",
        gt=0,
    )
    sweep_min_change_pct: float = _field(
        3, Category.INTRADAY, "Intraday sweep: and a move of at least this % on the day", ge=0
    )

    # --- Alerts (spec §7.2-7.3) ---------------------------------------------------------------
    alert_cooldown_minutes: int = _field(
        390, Category.ALERTS, "Minimum minutes between repeats of the same alert", ge=0
    )
    alert_min_grade: Literal["A+", "A", "B", "C"] = _field(
        "A",
        Category.ALERTS,
        "Setup alerts (near pivot, breakouts, pocket pivots, gaps, RS highs) for stocks you "
        "don't hold or watch: only at this grade or better",
    )
    alerts_email_enabled: bool = _field(True, Category.ALERTS, "Send alerts by email")
    alert_email_immediate_priority: Literal["high", "normal"] = _field(
        "high",
        Category.ALERTS,
        "Email alerts of this priority or higher straight away; the rest go in the digest",
    )
    quiet_hours_start: str = _field(
        "",
        Category.ALERTS,
        "No immediate emails from this time (US/Eastern, HH:MM; empty = no quiet hours). "
        "Held alerts go in the next digest",
        pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$",
    )
    quiet_hours_end: str = _field(
        "",
        Category.ALERTS,
        "Quiet hours end (US/Eastern, HH:MM)",
        pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$",
    )
    daily_digest_enabled: bool = _field(
        True, Category.ALERTS, "Email a digest of the day's alerts and setups after the close"
    )
    daily_digest_time: str = _field(
        "17:30",
        Category.ALERTS,
        "When the daily digest goes out (US/Eastern, HH:MM)",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    weekly_digest_enabled: bool = _field(
        True, Category.ALERTS, "Email a weekly review on Sunday evening"
    )

    # --- Backtest lab defaults (spec §11; a run can override each) ----------------------------
    backtest_years: int = _field(
        5,
        Category.BACKTEST,
        "Default backtest length (years, ending at the latest session)",
        ge=1,
        le=30,
    )
    backtest_max_positions: int = _field(
        10, Category.BACKTEST, "Most positions open at once", ge=1, le=100
    )
    backtest_slippage_pct: float = _field(
        0.1, Category.BACKTEST, "Slippage on every fill (% of the price)", ge=0, le=5
    )
    backtest_commission: float = _field(
        0, Category.BACKTEST, "Commission per order (account currency)", ge=0
    )
    backtest_min_grade: Literal["A+", "A", "B", "C"] = _field(
        "A", Category.BACKTEST, "Trade setups graded this or better (when the order is placed)"
    )
    backtest_skip_risk_too_wide: bool = _field(
        True, Category.BACKTEST, "Skip setups whose logical stop is wider than the maximum stop"
    )
    backtest_skip_correction: bool = _field(
        False, Category.BACKTEST, "No new entries while the market is in a correction"
    )
    backtest_sell_unconfirmed: bool = _field(
        True,
        Category.BACKTEST,
        "Sell at the close of the entry day when the breakout isn't confirmed (volume and close "
        "in range, as the lifecycle judges it)",
    )
    backtest_trailing_exit: Literal["sma50", "ema21", "none"] = _field(
        "sma50", Category.BACKTEST, "Sell on a close below this average (from the day after entry)"
    )
    backtest_time_stop_sessions: int = _field(
        15, Category.BACKTEST, "Time stop: check after this many sessions (0 = off)", ge=0, le=250
    )
    backtest_time_stop_min_gain_pct: float = _field(
        5, Category.BACKTEST, "Time stop: sell if the close is less than this % above entry", ge=0
    )
    backtest_partial_fraction_pct: float = _field(
        33.33,
        Category.BACKTEST,
        "Sell this % of the shares at the partial-profit gain (profit_take_min_pct; 0 = off)",
        ge=0,
        le=100,
    )
    backtest_in_sample_pct: float = _field(
        70,
        Category.BACKTEST,
        "In-sample share of the period (the rest is out of sample)",
        gt=0,
        lt=100,
    )
    backtest_grid_volume_pct: list[float] = _field(
        [100, 120, 140, 160, 180, 200],
        Category.BACKTEST,
        "Sensitivity grid: breakout volume thresholds (% of the 50-day average)",
        min_length=1,
        max_length=8,
    )
    backtest_grid_vcp_final_pct: list[float] = _field(
        [6, 8, 10, 12, 14],
        Category.BACKTEST,
        "Sensitivity grid: VCP maximum final contraction (%)",
        min_length=1,
        max_length=8,
    )

    # --- Data (spec §5.5) ---------------------------------------------------------------------
    backfill_years: int = _field(
        10, Category.DATA, "Years of daily history to backfill", ge=2, le=30
    )
    insider_history_quarters: int = _field(
        8,
        Category.DATA,
        "Quarters of insider transactions to load from SEC's bulk data sets",
        ge=1,
        le=80,
    )

    @model_validator(mode="after")
    def _check_ranges(self) -> "AppSettings":
        if self.vcp_min_contractions > self.vcp_max_contractions:
            raise ValueError("vcp_min_contractions must not exceed vcp_max_contractions")
        for low, high in (
            ("vcp_min_weeks", "vcp_max_weeks"),
            ("cup_min_depth_pct", "cup_max_depth_pct"),
            ("cup_max_depth_pct", "cup_bear_market_max_depth_pct"),
            ("cup_min_weeks", "cup_max_weeks"),
            ("handle_min_depth_pct", "handle_max_depth_pct"),
            ("htf_flag_min_depth_pct", "htf_flag_max_depth_pct"),
            ("htf_flag_min_weeks", "htf_flag_max_weeks"),
            ("ascending_pullback_min_pct", "ascending_pullback_max_pct"),
            ("ascending_min_weeks", "ascending_max_weeks"),
            ("eps_growth_q_min", "eps_growth_q_strong"),
            ("sales_growth_q_min", "sales_growth_q_strong"),
            ("eps_growth_annual_min", "eps_growth_annual_strong"),
            ("profit_take_min_pct", "profit_take_max_pct"),
        ):
            if getattr(self, low) > getattr(self, high):
                raise ValueError(f"{low} must not exceed {high}")
        if bool(self.quiet_hours_start) != bool(self.quiet_hours_end):
            raise ValueError("Set both quiet_hours_start and quiet_hours_end, or neither")
        if self.breakout_volume_min_pct_of_avg > self.breakout_volume_strong_pct_of_avg:
            raise ValueError(
                "breakout_volume_min_pct_of_avg must not exceed breakout_volume_strong_pct_of_avg"
            )
        if not (
            self.regime_confirmed_max_distribution_days
            < self.regime_pressure_distribution_days
            <= self.regime_correction_distribution_days
        ):
            raise ValueError(
                "Distribution-day thresholds must satisfy confirmed max < pressure <= correction"
            )
        return self


DEFAULTS = AppSettings()


def category_of(key: str) -> Category:
    extra = AppSettings.model_fields[key].json_schema_extra
    assert isinstance(extra, dict)
    return Category(str(extra["category"]))

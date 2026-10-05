import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.settings import store
from app.settings.schema import DEFAULTS, AppSettings, Category, category_of


def test_defaults_match_the_spec() -> None:
    # Spec §14, plus the owner's account defaults ($100,000 at 1% risk, 25% max position).
    assert DEFAULTS.min_price == 10
    assert DEFAULTS.min_avg_dollar_volume_50d == 20_000_000
    assert DEFAULTS.min_market_cap == 1_000_000_000
    assert DEFAULTS.rs_rating_min == 70
    assert (DEFAULTS.vcp_min_contractions, DEFAULTS.vcp_max_contractions) == (2, 6)
    assert DEFAULTS.vcp_contraction_ratio_max == 0.7
    assert DEFAULTS.breakout_volume_min_pct_of_avg == 140
    assert DEFAULTS.alert_cooldown_minutes == 390
    assert DEFAULTS.regime_multipliers.model_dump() == {
        "confirmed_uptrend": 1.0,
        "uptrend_under_pressure": 0.8,
        "correction": 0.5,
    }
    assert DEFAULTS.account_size == 100_000
    assert DEFAULTS.risk_per_trade_pct == 1.0
    assert DEFAULTS.max_position_pct == 25
    assert DEFAULTS.include_adrs is True


def test_phase_3_defaults_follow_the_spec_and_the_approved_plan() -> None:
    # Spec §6.5 / §6.7 / §6.8 numbers, and the grade points approved with the Phase 3 plan.
    assert DEFAULTS.swing_atr_multiple == 1.5
    assert (DEFAULTS.cup_min_depth_pct, DEFAULTS.cup_max_depth_pct) == (12, 33)
    assert DEFAULTS.cup_bear_market_max_depth_pct == 50
    assert (DEFAULTS.handle_min_depth_pct, DEFAULTS.handle_max_depth_pct) == (5, 12)
    assert (DEFAULTS.htf_min_gain_pct, DEFAULTS.htf_max_pole_weeks) == (90, 8)
    assert (DEFAULTS.htf_flag_min_depth_pct, DEFAULTS.htf_flag_max_depth_pct) == (10, 25)
    assert DEFAULTS.three_weeks_tight_pct == 1.5
    assert (DEFAULTS.ascending_min_weeks, DEFAULTS.ascending_max_weeks) == (9, 16)
    assert DEFAULTS.pocket_pivot_lookback_days == 10
    assert DEFAULTS.grade_weights.model_dump() == {
        "eps_growth": 25,
        "eps_acceleration": 10,
        "sales_growth": 15,
        "annual_eps_growth": 20,
        "roe": 10,
        "margins": 10,
        "accumulation": 10,
        "insider_bonus": 5,
    }
    assert DEFAULTS.revenue_grade_weights.model_dump() == {
        "sales_growth": 40,
        "sales_acceleration": 20,
        "margins": 20,
        "accumulation": 20,
    }
    assert DEFAULTS.grade_cutoffs.model_dump() == {"a": 80, "b": 65, "c": 50, "d": 35}
    assert (DEFAULTS.insider_cluster_min_insiders, DEFAULTS.insider_cluster_window_days) == (2, 30)


def test_every_setting_has_a_category_and_description() -> None:
    for key, field in AppSettings.model_fields.items():
        assert isinstance(category_of(key), Category)
        assert field.description, key


def test_invalid_combinations_are_rejected() -> None:
    with pytest.raises(ValidationError, match="vcp_min_contractions"):
        AppSettings(vcp_min_contractions=5, vcp_max_contractions=3)
    with pytest.raises(ValidationError):
        AppSettings(risk_per_trade_pct=0)
    with pytest.raises(ValidationError, match="handle_min_depth_pct must not exceed"):
        AppSettings(handle_min_depth_pct=15)
    with pytest.raises(ValidationError, match="eps_growth_q_min must not exceed"):
        AppSettings(eps_growth_q_min=50)
    with pytest.raises(ValidationError, match="A > B > C > D"):
        AppSettings.model_validate({"grade_cutoffs": {"a": 60, "b": 65, "c": 50, "d": 35}})


@pytest.mark.integration
async def test_seed_inserts_defaults_once_and_never_overwrites(db: AsyncSession) -> None:
    added = await store.seed(db)
    assert set(added) == set(AppSettings.model_fields)

    await store.update(db, {"account_size": 250_000})
    assert await store.seed(db) == []

    settings = await store.load(db)
    assert settings.account_size == 250_000


@pytest.mark.integration
async def test_update_validates_before_writing(db: AsyncSession) -> None:
    await store.seed(db)

    with pytest.raises(ValidationError):
        await store.update(db, {"risk_per_trade_pct": 2.0, "max_position_pct": 500})

    settings = await store.load(db)
    assert settings.risk_per_trade_pct == 1.0
    assert settings.max_position_pct == 25


@pytest.mark.integration
async def test_unknown_keys_are_rejected(db: AsyncSession) -> None:
    with pytest.raises(store.UnknownSettingError, match="not_a_setting"):
        await store.update(db, {"not_a_setting": 1})


@pytest.mark.integration
async def test_nested_values_round_trip_and_reset(db: AsyncSession) -> None:
    await store.seed(db)
    await store.update(
        db,
        {
            "regime_multipliers": {
                "confirmed_uptrend": 1.0,
                "uptrend_under_pressure": 0.7,
                "correction": 0.3,
            }
        },
    )
    assert (await store.load(db)).regime_multipliers.correction == 0.3

    await store.reset(db, ["regime_multipliers"])
    assert (await store.load(db)).regime_multipliers.correction == 0.5


@pytest.mark.integration
async def test_load_ignores_keys_that_no_longer_exist(db: AsyncSession) -> None:
    db.add(Setting(key="retired_setting", value=42))
    await db.commit()

    settings = await store.load(db)

    assert settings == DEFAULTS
    assert (await db.execute(select(Setting.key))).scalars().all() == ["retired_setting"]

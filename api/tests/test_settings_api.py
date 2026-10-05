import httpx
import pytest

from app.settings.schema import AppSettings


@pytest.mark.integration
async def test_lists_every_setting_with_value_default_and_constraints(
    signed_in: httpx.AsyncClient,
) -> None:
    response = await signed_in.get("/api/settings")

    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["items"]}
    assert set(items) == set(AppSettings.model_fields)
    risk = items["risk_per_trade_pct"]
    assert risk["value"] == risk["default"] == 1.0
    assert risk["category"] == "risk"
    assert risk["constraints"] == {"type": "number", "exclusiveMinimum": 0, "maximum": 10}


@pytest.mark.integration
async def test_update_then_reset(signed_in: httpx.AsyncClient) -> None:
    updated = await signed_in.patch(
        "/api/settings", json={"changes": {"account_size": 50_000, "small_cap_mode": True}}
    )
    assert updated.status_code == 200
    values = {i["key"]: i["value"] for i in updated.json()["items"]}
    assert values["account_size"] == 50_000
    assert values["small_cap_mode"] is True

    reset = await signed_in.post("/api/settings/reset", json={"keys": ["account_size"]})
    values = {i["key"]: i["value"] for i in reset.json()["items"]}
    assert values["account_size"] == 100_000
    assert values["small_cap_mode"] is True


@pytest.mark.integration
async def test_invalid_values_explain_what_is_wrong(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.patch("/api/settings", json={"changes": {"max_position_pct": 150}})

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"key": "max_position_pct", "message": "Input should be less than or equal to 100"}
    ]


@pytest.mark.integration
async def test_unknown_keys_are_a_400(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.patch("/api/settings", json={"changes": {"bogus": 1}})
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown setting: bogus"}

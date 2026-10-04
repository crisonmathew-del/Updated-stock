from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from app.api.routes.health import ComponentStatus, build_readiness
from app.core.heartbeat import BACKGROUND_SERVICES, beat
from app.core.redis import get_redis
from app.main import app

NOW = datetime(2026, 10, 5, 14, 30, tzinfo=UTC)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_liveness_needs_no_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "api"


def test_readiness_is_ok_only_when_every_component_is_ok() -> None:
    healthy = {"api": ComponentStatus(ok=True), "redis": ComponentStatus(ok=True)}
    assert build_readiness(healthy, NOW).status == "ok"

    one_down = {**healthy, "worker": ComponentStatus(ok=False, detail="no recent heartbeat")}
    assert build_readiness(one_down, NOW).status == "degraded"


@pytest.mark.integration
@pytest.mark.usefixtures("migrated_db", "clean_redis")
async def test_readiness_reports_every_component_ok(client: httpx.AsyncClient) -> None:
    for service in BACKGROUND_SERVICES:
        await beat(get_redis(), service)

    response = await client.get("/api/health/ready")

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["components"]) == {
        "api",
        "postgres",
        "timescaledb",
        "redis",
        "worker",
        "scheduler",
        "streamer",
    }
    assert body["components"]["postgres"]["detail"].startswith("PostgreSQL 16")
    assert body["components"]["timescaledb"]["detail"].startswith("TimescaleDB")


@pytest.mark.integration
@pytest.mark.usefixtures("migrated_db", "clean_redis")
async def test_readiness_is_503_when_a_service_stops_beating(client: httpx.AsyncClient) -> None:
    await beat(get_redis(), "worker")
    await beat(get_redis(), "scheduler")

    response = await client.get("/api/health/ready")

    assert response.status_code == 503
    components = response.json()["components"]
    assert components["streamer"] == {
        "ok": False,
        "detail": "no recent heartbeat",
        "latency_ms": None,
        "last_seen": None,
    }
    assert components["worker"]["ok"] is True
    assert components["postgres"]["ok"] is True

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.api.routes.health import ComponentStatus, backup_status, build_readiness
from app.core.config import get_settings
from app.core.heartbeat import BACKGROUND_SERVICES, beat
from app.core.redis import get_redis

NOW = datetime(2026, 10, 5, 14, 30, tzinfo=UTC)


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


def test_the_last_backup_must_be_recent_and_copied_off_site_when_configured() -> None:
    def last(hours_ago: float, offsite: str = "copied") -> str:
        finished = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
        return (
            f'{{"file": "breakout_2026-10-05_0230.dump", "finished_at": "{finished}", '
            f'"size_kb": 12595, "offsite": "{offsite}"}}'
        )

    fresh = backup_status(last(7.6), NOW, 26)
    assert fresh.ok
    assert fresh.detail == "breakout_2026-10-05_0230.dump, 12.3 MB, 8 h ago, copied off-site"
    assert backup_status(last(7.6, "skipped"), NOW, 26).detail == (
        "breakout_2026-10-05_0230.dump, 12.3 MB, 8 h ago"
    )
    stale = backup_status(last(30), NOW, 26)
    assert (stale.ok, stale.detail) == (
        False,
        "breakout_2026-10-05_0230.dump, 12.3 MB, 30 h ago: older than 26 h",
    )
    assert not backup_status(last(2, "failed"), NOW, 26).ok
    assert backup_status(None, NOW, 26).detail == "no backup yet: run `make backup-now`"
    assert not backup_status("{", NOW, 26).ok


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


@pytest.mark.integration
@pytest.mark.usefixtures("migrated_db", "clean_redis")
async def test_readiness_reports_the_backups_when_configured(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for service in BACKGROUND_SERVICES:
        await beat(get_redis(), service)
    status_file = tmp_path / "last.json"
    monkeypatch.setattr(get_settings(), "backup_status_file", str(status_file))

    missing = await client.get("/api/health/ready")
    assert missing.status_code == 503
    assert missing.json()["components"]["backups"]["detail"].startswith("no backup yet")

    finished = datetime.now(UTC).isoformat()
    status_file.write_text(
        f'{{"file": "b.dump", "finished_at": "{finished}", "size_kb": 10, "offsite": "skipped"}}'
    )
    fresh = await client.get("/api/health/ready")
    assert fresh.status_code == 200
    assert fresh.json()["components"]["backups"]["ok"] is True

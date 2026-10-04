"""Health endpoints.

- `GET /api/health`: liveness. Is the API process up? No dependencies touched.
- `GET /api/health/ready`: readiness. Checks Postgres, the TimescaleDB extension, Redis and the
  heartbeats of the background services. Returns 503 if any component is down.
"""

import asyncio
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.db import get_engine
from app.core.heartbeat import BACKGROUND_SERVICES, is_alive, last_seen
from app.core.redis import get_redis

CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "api"
    version: str = __version__


class ComponentStatus(BaseModel):
    ok: bool
    detail: str | None = None
    latency_ms: float | None = None
    last_seen: datetime | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checked_at: datetime
    components: dict[str, ComponentStatus]


def build_readiness(components: dict[str, ComponentStatus], now: datetime) -> ReadinessResponse:
    healthy = all(c.ok for c in components.values())
    return ReadinessResponse(
        status="ok" if healthy else "degraded", checked_at=now, components=components
    )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def _check_database() -> dict[str, ComponentStatus]:
    start = time.perf_counter()
    try:
        async with get_engine().connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
            timescale = (
                await conn.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                )
            ).scalar_one_or_none()
    except Exception as exc:
        down = ComponentStatus(ok=False, detail=f"unreachable: {type(exc).__name__}")
        return {"postgres": down, "timescaledb": ComponentStatus(ok=False, detail="postgres down")}

    latency = _elapsed_ms(start)
    timescale_status = (
        ComponentStatus(ok=True, detail=f"TimescaleDB {timescale}")
        if timescale
        else ComponentStatus(ok=False, detail="extension not installed; run `make migrate`")
    )
    return {
        "postgres": ComponentStatus(ok=True, detail=f"PostgreSQL {version}", latency_ms=latency),
        "timescaledb": timescale_status,
    }


async def _check_redis_and_heartbeats(now: datetime) -> dict[str, ComponentStatus]:
    redis = get_redis()
    start = time.perf_counter()
    try:
        await redis.ping()
        latency = _elapsed_ms(start)
        seen = await last_seen(redis, BACKGROUND_SERVICES)
    except Exception as exc:
        result = {"redis": ComponentStatus(ok=False, detail=f"unreachable: {type(exc).__name__}")}
        for service in BACKGROUND_SERVICES:
            result[service] = ComponentStatus(ok=False, detail="redis down")
        return result

    result = {"redis": ComponentStatus(ok=True, latency_ms=latency)}
    for service in BACKGROUND_SERVICES:
        alive = is_alive(seen[service], now)
        result[service] = ComponentStatus(
            ok=alive,
            detail=None if alive else "no recent heartbeat",
            last_seen=seen[service],
        )
    return result


async def _with_timeout(
    names: tuple[str, ...], check: Awaitable[dict[str, ComponentStatus]]
) -> dict[str, ComponentStatus]:
    try:
        return await asyncio.wait_for(check, timeout=CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return {name: ComponentStatus(ok=False, detail="check timed out") for name in names}


@router.get("", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    now = datetime.now(UTC)
    db_result, redis_result = await asyncio.gather(
        _with_timeout(("postgres", "timescaledb"), _check_database()),
        _with_timeout(("redis", *BACKGROUND_SERVICES), _check_redis_and_heartbeats(now)),
    )
    components = {
        "api": ComponentStatus(ok=True, detail=f"v{__version__}"),
        **db_result,
        **redis_result,
    }
    result = build_readiness(components, now)
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result

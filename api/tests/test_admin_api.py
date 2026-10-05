from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import close_queue, get_queue
from app.core.redis import get_redis
from app.data.quality import Check, Issue, Severity, reconcile
from tests.test_eod import seeded_universe

ADMIN_GETS = (
    "/api/admin/universe",
    "/api/admin/backfill",
    "/api/admin/data-health",
    "/api/admin/jobs",
)


@pytest.mark.integration
@pytest.mark.usefixtures("db", "clean_redis")
async def test_admin_needs_a_session(client: httpx.AsyncClient) -> None:
    for path in ADMIN_GETS:
        assert (await client.get(path)).status_code == 401, path
    assert (await client.post("/api/admin/backfill", json={})).status_code == 401


@pytest.mark.integration
async def test_universe_summary_and_backfill_progress(
    signed_in: httpx.AsyncClient, db: AsyncSession
) -> None:
    await seeded_universe(db)

    universe = (await signed_in.get("/api/admin/universe")).json()
    assert universe["active"] == 18
    assert (universe["stocks"], universe["benchmarks"]) == (2, 16)
    assert universe["by_type"] == {"common": 2, "etf": 15, "index": 1}
    assert universe["backfill"] == {"done": 18, "pending": 0, "failed": 0, "no_data": 0}
    assert universe["latest_bar"] == "2026-09-25"

    backfill = (await signed_in.get("/api/admin/backfill")).json()
    assert backfill["status"] == "succeeded"
    assert backfill["processed"] == backfill["total"] == 18


@pytest.mark.integration
async def test_data_health_lists_open_issues_worst_first(
    signed_in: httpx.AsyncClient, db: AsyncSession
) -> None:
    await reconcile(
        db,
        [
            Issue(Check.SHORT_HISTORY, Severity.INFO, "Only 40 sessions", ticker_id=None),
            Issue(Check.DATASET_STALE, Severity.CRITICAL, "No bars for 2026-10-02 yet"),
            Issue(Check.SEC_NOT_CONFIGURED, Severity.WARNING, "SEC_USER_AGENT is not set"),
        ],
        datetime(2026, 10, 2, 20, tzinfo=UTC),
    )

    health = (await signed_in.get("/api/admin/data-health")).json()

    assert health["summary"] == {"critical": 1, "warning": 1, "info": 1}
    assert [i["severity"] for i in health["issues"]] == ["critical", "warning", "info"]
    assert health["by_check"]["dataset_stale"] == {"critical": 1}
    only_critical = (await signed_in.get("/api/admin/data-health?severity=critical")).json()
    assert [i["check"] for i in only_critical["issues"]] == ["dataset_stale"]


@pytest.mark.integration
async def test_buttons_enqueue_jobs_unless_a_data_job_is_running(
    signed_in: httpx.AsyncClient,
) -> None:
    response = await signed_in.post("/api/admin/backfill", json={"symbols": ["aapl"], "years": 3})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    queue = await get_queue()
    queued = {j.job_id: j for j in await queue.queued_jobs()}
    assert queued[job_id].function == "backfill"
    assert queued[job_id].args == ("api", 3, ["AAPL"], False)

    await get_redis().set("lock:ingest", "someone", ex=60)
    blocked = await signed_in.post("/api/admin/eod-update")
    assert blocked.status_code == 409
    assert "is running" in blocked.json()["detail"]
    # Quality checks only read market data, so they don't wait for the ingest lock.
    assert (await signed_in.post("/api/admin/data-quality")).status_code == 202
    await close_queue()

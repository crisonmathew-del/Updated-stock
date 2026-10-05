"""Admin endpoints behind the data page: universe and backfill status, data health, job history,
and buttons that enqueue data jobs (spec §5.5, §12)."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, case, func, select

from app.api.deps import DbSession, RedisClient, current_user
from app.core.queue import get_queue
from app.data.backfill import read_progress
from app.data.jobs import FUNDAMENTALS_LOCK, INGEST_LOCK
from app.models import DataQualityIssue, JobRun, Ticker

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(current_user)])

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class JobRunOut(BaseModel):
    id: int
    job_name: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    stats: dict[str, Any]


class BackfillCounts(BaseModel):
    done: int = 0
    pending: int = 0
    failed: int = 0
    no_data: int = 0


class UniverseSummary(BaseModel):
    active: int
    inactive: int
    stocks: int
    benchmarks: int
    by_type: dict[str, int]
    by_exchange: dict[str, int]
    with_cik: int
    with_sic: int
    with_market_cap: int
    backfill: BackfillCounts
    earliest_bar: date | None
    latest_bar: date | None
    last_build: JobRunOut | None


class BackfillStatus(BaseModel):
    status: str
    run_id: int | None
    total: int
    processed: int
    done: int
    no_data: int
    failed: int
    bars_written: int
    start: str | None
    end: str | None
    started_at: str | None
    updated_at: str | None
    message: str | None
    current: list[str]


class IssueOut(BaseModel):
    id: int
    check: str
    severity: str
    symbol: str | None
    issue_date: date | None
    detail: str
    first_detected_at: datetime
    last_detected_at: datetime


class DataHealth(BaseModel):
    summary: dict[str, int]
    by_check: dict[str, dict[str, int]]
    issues: list[IssueOut]
    last_check: JobRunOut | None


class BackfillRequest(BaseModel):
    years: int | None = Field(default=None, ge=1, le=30)
    symbols: list[str] | None = None
    force: bool = False


class UniverseRequest(BaseModel):
    then_backfill: bool = True


class Enqueued(BaseModel):
    job: str
    job_id: str


async def _latest_run(db: DbSession, *names: str) -> JobRunOut | None:
    run = await db.scalar(
        select(JobRun).where(JobRun.job_name.in_(names)).order_by(JobRun.started_at.desc()).limit(1)
    )
    return JobRunOut.model_validate(run, from_attributes=True) if run else None


async def _grouped(db: DbSession, query: Select[str, int]) -> dict[str, int]:
    return {str(key): int(count) for key, count in (await db.execute(query)).all()}


@router.get("/universe", response_model=UniverseSummary)
async def universe_summary(db: DbSession) -> UniverseSummary:
    active = Ticker.active.is_(True)
    totals = (
        await db.execute(
            select(
                func.count().filter(active),
                func.count().filter(Ticker.active.is_(False)),
                func.count().filter(active, Ticker.is_benchmark.is_(True)),
                func.count().filter(active, Ticker.cik.is_not(None)),
                func.count().filter(active, Ticker.sic_code.is_not(None)),
                func.count().filter(active, Ticker.market_cap.is_not(None)),
                func.min(Ticker.bars_start).filter(active),
                func.max(Ticker.bars_end).filter(active),
            )
        )
    ).one()
    by_type = await _grouped(
        db, select(Ticker.type, func.count()).where(active).group_by(Ticker.type)
    )
    by_exchange = await _grouped(
        db, select(Ticker.exchange, func.count()).where(active).group_by(Ticker.exchange)
    )
    backfill = await _grouped(
        db,
        select(Ticker.backfill_status, func.count()).where(active).group_by(Ticker.backfill_status),
    )
    return UniverseSummary(
        active=totals[0],
        inactive=totals[1],
        benchmarks=totals[2],
        stocks=totals[0] - totals[2],
        with_cik=totals[3],
        with_sic=totals[4],
        with_market_cap=totals[5],
        earliest_bar=totals[6],
        latest_bar=totals[7],
        by_type=by_type,
        by_exchange=by_exchange,
        backfill=BackfillCounts(**backfill),
        last_build=await _latest_run(db, "universe"),
    )


@router.get("/backfill", response_model=BackfillStatus)
async def backfill_status(redis: RedisClient) -> BackfillStatus:
    progress = await read_progress(redis)
    return BackfillStatus(processed=progress.processed, **progress.__dict__)


@router.get("/data-health", response_model=DataHealth)
async def data_health(
    db: DbSession,
    severity: Literal["critical", "warning", "info"] | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> DataHealth:
    open_issue = DataQualityIssue.resolved_at.is_(None)
    rows = await db.execute(
        select(DataQualityIssue.check, DataQualityIssue.severity, func.count())
        .where(open_issue)
        .group_by(DataQualityIssue.check, DataQualityIssue.severity)
    )
    by_check: dict[str, dict[str, int]] = {}
    summary = dict.fromkeys(SEVERITY_ORDER, 0)
    for check, sev, count in rows.all():
        by_check.setdefault(check, {})[sev] = count
        summary[sev] = summary.get(sev, 0) + count

    rank = case(SEVERITY_ORDER, value=DataQualityIssue.severity, else_=9)
    query = (
        select(DataQualityIssue, Ticker.symbol)
        .outerjoin(Ticker, Ticker.id == DataQualityIssue.ticker_id)
        .where(open_issue)
        .order_by(rank, DataQualityIssue.check, Ticker.symbol, DataQualityIssue.issue_date.desc())
        .limit(limit)
    )
    if severity:
        query = query.where(DataQualityIssue.severity == severity)
    issues = [
        IssueOut(
            id=issue.id,
            check=issue.check,
            severity=issue.severity,
            symbol=symbol,
            issue_date=issue.issue_date,
            detail=issue.detail,
            first_detected_at=issue.first_detected_at,
            last_detected_at=issue.last_detected_at,
        )
        for issue, symbol in (await db.execute(query)).all()
    ]
    return DataHealth(
        summary=summary,
        by_check=by_check,
        issues=issues,
        last_check=await _latest_run(db, "data_quality", "eod_update"),
    )


@router.get("/jobs", response_model=list[JobRunOut])
async def job_runs(db: DbSession, limit: int = Query(25, ge=1, le=200)) -> list[JobRunOut]:
    runs = await db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit))
    return [JobRunOut.model_validate(r, from_attributes=True) for r in runs]


async def _enqueue(redis: RedisClient, job: str, *args: Any, ingest: bool = True) -> Enqueued:
    if ingest and await redis.exists(f"lock:{INGEST_LOCK}"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another data job (universe, backfill or EOD update) is running. Try again when "
            "it finishes.",
        )
    job_id = f"{job}:{uuid.uuid4().hex[:12]}"
    queue = await get_queue()
    await queue.enqueue_job(job, "api", *args, _job_id=job_id)
    return Enqueued(job=job, job_id=job_id)


@router.post("/backfill", response_model=Enqueued, status_code=status.HTTP_202_ACCEPTED)
async def start_backfill(body: BackfillRequest, redis: RedisClient) -> Enqueued:
    symbols = [s.strip().upper() for s in body.symbols] if body.symbols else None
    return await _enqueue(redis, "backfill", body.years, symbols, body.force)


@router.post("/universe", response_model=Enqueued, status_code=status.HTTP_202_ACCEPTED)
async def rebuild_universe(body: UniverseRequest, redis: RedisClient) -> Enqueued:
    return await _enqueue(redis, "universe", body.then_backfill)


@router.post("/eod-update", response_model=Enqueued, status_code=status.HTTP_202_ACCEPTED)
async def start_eod_update(redis: RedisClient) -> Enqueued:
    return await _enqueue(redis, "eod_update")


class AnalyticsRequest(BaseModel):
    full: bool = False


@router.post("/analytics", response_model=Enqueued, status_code=status.HTTP_202_ACCEPTED)
async def start_analytics(body: AnalyticsRequest, redis: RedisClient) -> Enqueued:
    return await _enqueue(redis, "analytics", body.full)


class FundamentalsRequest(BaseModel):
    full: bool = False
    symbols: list[str] | None = None


@router.post("/fundamentals", response_model=Enqueued, status_code=status.HTTP_202_ACCEPTED)
async def start_fundamentals(body: FundamentalsRequest, redis: RedisClient) -> Enqueued:
    if await redis.exists(f"lock:{FUNDAMENTALS_LOCK}"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A fundamentals load is already running. Try again when it finishes.",
        )
    symbols = [s.strip().upper() for s in body.symbols] if body.symbols else None
    return await _enqueue(redis, "fundamentals", body.full, symbols, ingest=False)


@router.post("/data-quality", response_model=Enqueued, status_code=status.HTTP_202_ACCEPTED)
async def start_data_quality(redis: RedisClient) -> Enqueued:
    return await _enqueue(redis, "data_quality", ingest=False)

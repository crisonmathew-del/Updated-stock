"""The backtest lab (spec §8.9, §11): start runs in the worker, follow their progress, read
their reports. Every report is labelled hypothetical and states the survivorship bias.

- GET    /backtests/options              the defaults (from settings), data range, patterns,
                                         saved screens, the sensitivity grid
- GET    /backtests                      runs, newest first (status, progress, headline numbers)
- POST   /backtests                      start a run: 202 with the queued run
- GET    /backtests/{id}                 one run with its report and trades
- DELETE /backtests/{id}
- GET    /backtests/{id}/trades/{n}/chart  daily bars around trade n, with its fills
One run at a time: starting another while one is queued or running is refused (409).
"""

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession, current_user
from app.backtest.engine import BacktestParams, Exits, Portfolio, Rules
from app.core.calendar import sessions_between
from app.core.queue import get_queue
from app.data.loaders import query_frame
from app.models import BacktestRun, IndicatorDaily, SavedScreen, Ticker
from app.patterns.types import EVENTS, LABELS, PatternType
from app.settings import store

router = APIRouter(prefix="/backtests", tags=["backtests"], dependencies=[Depends(current_user)])

ACTIVE = ("queued", "running")
CHART_BEFORE = 60  # sessions of context before the entry
CHART_AFTER = 20  # and after the exit
MIN_SESSIONS = 20
PATTERNS = [
    {"value": str(t), "label": LABELS[t]}
    for t in PatternType
    if t not in EVENTS or t == PatternType.EARNINGS_GAP
]


class Choice(BaseModel):
    value: str
    label: str


class ScreenChoice(BaseModel):
    id: int
    name: str


class OptionsOut(BaseModel):
    defaults: BacktestParams
    first_date: date | None  # the first session with analytics
    last_date: date | None
    patterns: list[Choice]
    screens: list[ScreenChoice]
    grid: dict[str, list[float]]
    running: int | None


class RunRequest(BaseModel):
    name: str | None = Field(None, max_length=120)
    start: date | None = None
    end: date | None = None
    rules: Rules | None = None
    portfolio: Portfolio | None = None
    exits: Exits | None = None
    sensitivity: bool = False
    screen_id: int | None = None


class RunOut(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    start: date
    end: date
    sensitivity: bool
    screen: str | None
    summary: dict[str, Any] | None
    progress: dict[str, Any]
    error: str | None


class RunDetail(RunOut):
    params: BacktestParams
    report: dict[str, Any] | None
    trades: list[dict[str, Any]]


class TradeChart(BaseModel):
    symbol: str
    time: list[str]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    sma50: list[float | None]
    trade: dict[str, Any]


def _out(run: BacktestRun) -> RunOut:
    params = BacktestParams.model_validate(run.params)
    return RunOut(
        id=run.id,
        name=run.name,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        start=params.start,
        end=params.end,
        sensitivity=params.sensitivity,
        screen=params.rules.screen_name,
        summary=run.summary,
        progress=run.progress or {},
        error=run.error,
    )


async def _range(db: DbSession) -> tuple[date | None, date | None]:
    row = (
        await db.execute(select(func.min(IndicatorDaily.date), func.max(IndicatorDaily.date)))
    ).one()
    return row[0], row[1]


def default_start(first: date | None, last: date, years: int) -> date:
    """`years` before `last`, but not before a year of analytics (the 200-day average and the
    52-week range need it)."""
    try:
        start = last.replace(year=last.year - years)
    except ValueError:  # 29 February
        start = last.replace(year=last.year - years, day=28)
    if first is not None:
        start = max(start, first + timedelta(days=365))
    return min(start, last)


def _describe(params: BacktestParams) -> str:
    rules = params.rules
    grade = f"{rules.min_grade} or better" if rules.min_grade else "any grade"
    what = f"Setups graded {grade}" if rules.min_grade != "A+" else "A+ setups"
    if rules.screen_name:
        what += f" matching “{rules.screen_name}”"
    return f"{what}, {params.start.isoformat()} to {params.end.isoformat()}"


async def _running(db: DbSession, user_id: int) -> int | None:
    return await db.scalar(
        select(BacktestRun.id)
        .where(BacktestRun.user_id == user_id, BacktestRun.status.in_(ACTIVE))
        .order_by(BacktestRun.id.desc())
        .limit(1)
    )


@router.get("/options")
async def options(db: DbSession, user: AuthUser) -> OptionsOut:
    settings = await store.load(db)
    first, last = await _range(db)
    end = last or date.today()
    defaults = BacktestParams.defaults(
        settings, default_start(first, end, settings.backtest_years), end
    )
    screens = await db.execute(
        select(SavedScreen.id, SavedScreen.name)
        .where(SavedScreen.user_id == user.id)
        .order_by(SavedScreen.name)
    )
    return OptionsOut(
        defaults=defaults,
        first_date=first,
        last_date=last,
        patterns=[Choice(**p) for p in PATTERNS],
        screens=[ScreenChoice(id=i, name=n) for i, n in screens.all()],
        grid={
            "vcp": list(settings.backtest_grid_vcp_final_pct),
            "volume": list(settings.backtest_grid_volume_pct),
        },
        running=await _running(db, user.id),
    )


@router.get("")
async def list_runs(db: DbSession, user: AuthUser) -> list[RunOut]:
    runs = await db.scalars(
        select(BacktestRun)
        .where(BacktestRun.user_id == user.id)
        .order_by(BacktestRun.id.desc())
        .limit(100)
    )
    return [_out(r) for r in runs.all()]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: RunRequest, db: DbSession, user: AuthUser) -> RunOut:
    running = await _running(db, user.id)
    if running is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A backtest is already running. Wait for it to finish (one runs at a time).",
        )
    settings = await store.load(db)
    first, last = await _range(db)
    if last is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "No analytics yet: load prices and run the scan before backtesting.",
        )
    end = body.end or last
    start = body.start or default_start(first, end, settings.backtest_years)
    if end > last:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The data ends on {last.isoformat()}: pick an end date on or before it.",
        )
    if len(sessions_between(start, end)) < MIN_SESSIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Pick a period of at least {MIN_SESSIONS} sessions (the start must come before "
            "the end).",
        )
    params = BacktestParams.defaults(settings, start, end)
    updates: dict[str, Any] = {"sensitivity": body.sensitivity}
    for part in ("rules", "portfolio", "exits"):
        value = getattr(body, part)
        if value is not None:
            updates[part] = value
    params = params.model_copy(update=updates)
    if body.screen_id is not None:
        screen = await db.get(SavedScreen, body.screen_id)
        if screen is None or screen.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such saved screen.")
        params.rules = params.rules.model_copy(
            update={
                "screen_id": screen.id,
                "screen_name": screen.name,
                "screen_filters": list(screen.filters or []),
            }
        )
    params = BacktestParams.model_validate(params.model_dump(mode="json"))
    run = BacktestRun(
        user_id=user.id,
        name=(body.name or "").strip() or _describe(params),
        status="queued",
        params=params.model_dump(mode="json"),
        progress={"stage": "queued"},
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    queue = await get_queue()
    await queue.enqueue_job("backtest", "api", run.id, _job_id=f"backtest:{run.id}")
    return _out(run)


async def _own(db: DbSession, user_id: int, run_id: int) -> BacktestRun:
    run = await db.get(BacktestRun, run_id)
    if run is None or run.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such backtest.")
    return run


@router.get("/{run_id}")
async def get_run(run_id: int, db: DbSession, user: AuthUser) -> RunDetail:
    run = await _own(db, user.id, run_id)
    return RunDetail(
        **_out(run).model_dump(),
        params=BacktestParams.model_validate(run.params),
        report=run.report,
        trades=run.trades or [],
    )


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: int, db: DbSession, user: AuthUser) -> Response:
    run = await _own(db, user.id, run_id)
    if run.status == "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This backtest is running: delete it once it has finished."
        )
    await db.delete(run)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{run_id}/trades/{n}/chart")
async def trade_chart(run_id: int, n: int, db: DbSession, user: AuthUser) -> TradeChart:
    run = await _own(db, user.id, run_id)
    trades = run.trades or []
    if not 1 <= n <= len(trades):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"This backtest has no trade {n}.")
    trade = trades[n - 1]
    ticker = await db.get(Ticker, int(trade["ticker_id"]))
    if ticker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That stock is no longer in the database.")
    entry = date.fromisoformat(trade["entry_date"])
    exit_ = date.fromisoformat(trade["exit_date"] or trade["entry_date"])
    start = entry - timedelta(days=CHART_BEFORE * 7 // 5 + 10)
    end = exit_ + timedelta(days=CHART_AFTER * 7 // 5 + 5)
    frame = await query_frame(
        db,
        "SELECT b.date, b.open, b.high, b.low, b.close, i.sma50 FROM daily_bars b "
        "LEFT JOIN indicators_daily i ON i.ticker_id = b.ticker_id AND i.date = b.date "
        f"WHERE b.ticker_id = {int(ticker.id)} AND b.date BETWEEN '{start.isoformat()}' "
        f"AND '{end.isoformat()}' ORDER BY b.date",
    )
    days = frame["date"].to_list() if frame.height else []
    return TradeChart(
        symbol=ticker.symbol,
        time=[d.isoformat() if isinstance(d, date) else str(d) for d in days],
        open=frame["open"].to_list() if frame.height else [],
        high=frame["high"].to_list() if frame.height else [],
        low=frame["low"].to_list() if frame.height else [],
        close=frame["close"].to_list() if frame.height else [],
        sma50=frame["sma50"].to_list() if frame.height else [],
        trade=trade,
    )

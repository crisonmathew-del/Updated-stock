"""Backtest runs: build (or reuse) the candidate tape, simulate the portfolio, store the report.

A tape depends on the dates and every setting that changes what the scan finds (patterns,
scores, lifecycle, trade plans); it is reused by later runs with the same ones, so changing
only portfolio, exit or rule options re-runs in seconds. The sensitivity grid needs a tape with
one cell per (VCP limit, breakout volume) pair: the first such run builds it (slower).
"""

import asyncio
import hashlib
import json
import shutil
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.context import load_market
from app.backtest.engine import BacktestParams, Candidate, PriceBook, simulate
from app.backtest.reports import build_report, headline, heatmap_cell
from app.backtest.tape import Cell, Tape, base_cell, build_tape
from app.core.calendar import sessions_between
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.jobs import Trigger, job_lock, track_job
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.data.loaders import read_frame
from app.models import BacktestRun, BacktestTape
from app.scanner.screener_rows import matches as screen_matches
from app.settings import store
from app.settings.schema import AppSettings, Category

log = get_logger(__name__)

LOCK = "backtest"
# Settings that don't change what the scan finds (the tape): the lab's own options, alerts,
# the intraday watcher and data loading.
TAPE_NEUTRAL = {Category.BACKTEST, Category.ALERTS, Category.INTRADAY, Category.DATA}
PROGRESS_SECONDS = 2.0
FILES = ("candidates", "breakouts", "signals", "stock_days")


def tape_settings(settings: AppSettings) -> dict[str, Any]:
    out = {}
    for name, info in AppSettings.model_fields.items():
        extra = info.json_schema_extra
        category = extra.get("category") if isinstance(extra, dict) else None
        if category not in TAPE_NEUTRAL:
            out[name] = getattr(settings, name)
    encoded: dict[str, Any] = json.loads(
        json.dumps(out, default=lambda v: v.model_dump(mode="json"))
    )
    return encoded


def settings_hash(settings: AppSettings) -> str:
    raw = json.dumps(tape_settings(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def grid_cells(settings: AppSettings) -> list[Cell]:
    """The run's own cell first, then every grid point."""
    cells = [base_cell(settings)]
    for vcp in settings.backtest_grid_vcp_final_pct:
        for volume in settings.backtest_grid_volume_pct:
            cell = Cell(float(vcp), float(volume))
            if cell not in cells:
                cells.append(cell)
    return cells


# --- Tape storage -----------------------------------------------------------------------------


def tape_dir(tape_id: int) -> Path:
    return Path(get_settings().backtest_dir) / f"tape_{tape_id}"


def write_tape(tape: Tape, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        getattr(tape, name).write_parquet(path / f"{name}.parquet")


def read_tape(path: Path, stats: dict[str, Any]) -> Tape:
    candidates, breakouts, signals, stock_days = (
        pl.read_parquet(path / f"{name}.parquet") for name in FILES
    )
    return Tape(candidates, breakouts, signals, stock_days, stats=stats)


def _cell_dicts(cells: list[Cell]) -> list[dict[str, float]]:
    return [c.as_dict() for c in cells]


async def find_tape(
    session: AsyncSession, digest: str, start: date, end: date, cells: list[Cell]
) -> BacktestTape | None:
    """A finished tape for these dates and settings holding every cell, with its files."""
    rows = await session.scalars(
        select(BacktestTape)
        .where(
            BacktestTape.settings_hash == digest,
            BacktestTape.start == start,
            BacktestTape.end == end,
            BacktestTape.status == "done",
        )
        .order_by(BacktestTape.id.desc())
    )
    wanted = _cell_dicts(cells)
    for row in rows.all():
        held = row.cells
        complete = bool(held) and held[0] == wanted[0] and all(c in held for c in wanted)
        if complete and row.path and all((Path(row.path) / f"{n}.parquet").exists() for n in FILES):
            return row
    return None


async def ensure_tape(
    session: AsyncSession,
    settings: AppSettings,
    start: date,
    end: date,
    cells: list[Cell],
    report: Callable[[dict[str, Any]], None],
) -> tuple[BacktestTape, Tape, bool]:
    """Reuse or build the tape. Returns (row, tape, reused)."""
    digest = settings_hash(settings)
    found = await find_tape(session, digest, start, end, cells)
    if found is not None:
        return found, read_tape(Path(found.path or ""), found.stats), True
    row = BacktestTape(
        start=start,
        end=end,
        settings_hash=digest,
        settings=tape_settings(settings),
        cells=_cell_dicts(cells),
        status="running",
        stats={},
    )
    session.add(row)
    await session.commit()
    try:
        market = await load_market(start, end)
        state = {"done": 0, "total": len(market.tickers)}

        def progress(done: int, total: int) -> None:
            state.update(done=done, total=total)

        task = asyncio.create_task(
            asyncio.to_thread(build_tape, market, settings, cells, start, end, progress=progress)
        )
        while not task.done():
            report({"stage": "tape", **state})
            await asyncio.wait({task}, timeout=PROGRESS_SECONDS)
        tape = task.result()
        path = tape_dir(row.id)
        await asyncio.to_thread(write_tape, tape, path)
    except Exception as exc:
        row.status, row.error = "failed", str(exc)
        row.finished_at = datetime.now(UTC)
        await session.commit()
        raise
    row.status, row.path, row.stats = "done", str(path), tape.stats
    row.finished_at = datetime.now(UTC)
    await session.commit()
    return row, tape, False


# --- Stage 2 inputs ---------------------------------------------------------------------------


async def load_prices(ticker_ids: list[int], sessions: list[date]) -> PriceBook:
    """Daily prices and the trailing averages for these stocks, aligned to `sessions`."""
    n = len(sessions)
    book = PriceBook(sessions, {}, {}, {}, {}, {}, {})
    if not ticker_ids or not sessions:
        return book
    index = {d: i for i, d in enumerate(sessions)}
    for first in range(0, len(ticker_ids), 500):
        ids = ",".join(str(int(t)) for t in ticker_ids[first : first + 500])
        frame = await read_frame(
            "SELECT b.ticker_id, b.date, b.open, b.high, b.low, b.close, i.sma50, i.ema21 "
            "FROM daily_bars b LEFT JOIN indicators_daily i "
            "ON i.ticker_id = b.ticker_id AND i.date = b.date "
            f"WHERE b.ticker_id IN ({ids}) AND b.date BETWEEN '{sessions[0].isoformat()}' "
            f"AND '{sessions[-1].isoformat()}'"
        )
        if frame.is_empty():
            continue
        for (tid,), rows in frame.partition_by("ticker_id", as_dict=True).items():
            positions = np.array([index.get(d, -1) for d in rows["date"].to_list()])
            keep = positions >= 0
            for name, target in (
                ("open", book.open),
                ("high", book.high),
                ("low", book.low),
                ("close", book.close),
                ("sma50", book.sma50),
                ("ema21", book.ema21),
            ):
                values = np.full(n, np.nan)
                column = rows[name].cast(pl.Float64).fill_null(np.nan).to_numpy()
                values[positions[keep]] = column[keep]
                target[int(tid)] = values
    symbols = await read_frame(f"SELECT id, symbol FROM tickers WHERE id IN ({_ids(ticker_ids)})")
    book.symbols = {int(i): str(s) for i, s in symbols.iter_rows()}
    return book


async def benchmark_closes(sessions: list[date], symbol: str = "SPY") -> list[float | None]:
    if not sessions:
        return []
    frame = await read_frame(
        "SELECT b.date, b.close FROM daily_bars b JOIN tickers t ON t.id = b.ticker_id "
        f"WHERE t.symbol = '{symbol}' AND t.is_benchmark AND b.date BETWEEN "
        f"'{sessions[0].isoformat()}' AND '{sessions[-1].isoformat()}'"
    )
    closes = {d: float(c) for d, c in frame.iter_rows()}
    return [closes.get(d) for d in sessions]


def _ids(values: list[int]) -> str:
    return ",".join(str(int(v)) for v in values) or "NULL"


def cell_candidates(tape: Tape, cell: int, with_fields: bool) -> list[Candidate]:
    frame = tape.candidates.filter(pl.col("cell") == cell)
    if with_fields and not tape.stock_days.is_empty():
        frame = frame.join(tape.stock_days, on=["date", "ticker_id"], how="left")
    stock_fields = [c for c in tape.stock_days.columns if c not in ("date", "ticker_id")]
    out = []
    for r in frame.iter_rows(named=True):
        fields = None
        if with_fields:
            fields = {k: r.get(k) for k in stock_fields}
            fields.update(
                setup_state=r["state"],
                setup_kind=r["kind"],
                pattern=r["pattern"],
                grade=r["grade"],
                score=None if r["score"] is None else round(r["score"], 1),
                readiness_pct=r["readiness_pct"],
                pivot=r["pivot"],
                breakout_today=False,
                liquid=True,
            )
        out.append(
            Candidate(
                date=r["date"],
                ticker_id=r["ticker_id"],
                setup=r["setup"],
                pattern=r["pattern"],
                state=r["state"],
                pivot=r["pivot"],
                entry=r["entry"],
                stop=r["stop"],
                score=r["score"],
                grade=r["grade"],
                readiness_pct=r["readiness_pct"],
                regime=r["regime"],
                risk_too_wide=bool(r["risk_too_wide"]),
                fields=fields,
            )
        )
    return out


def cell_breakouts(tape: Tape, cell: int) -> set[tuple[date, int]]:
    frame = tape.breakouts.filter(pl.col("cell") == cell)
    return {(d, int(t)) for d, t in frame.select("date", "ticker_id").iter_rows()}


# --- The run ----------------------------------------------------------------------------------


async def _set(session: AsyncSession, run_id: int, **values: Any) -> None:
    await session.execute(update(BacktestRun).where(BacktestRun.id == run_id).values(**values))
    await session.commit()


async def run_backtest(run_id: int) -> dict[str, Any]:
    """Run a queued backtest: tape, simulation, report. Marks it failed on any error."""
    started = time.perf_counter()
    async with get_sessionmaker()() as session:
        run = await session.get(BacktestRun, run_id)
        if run is None:
            raise ValueError(f"No backtest run {run_id}.")
        params = BacktestParams.model_validate(run.params)
        settings = await store.load(session)
        await _set(session, run_id, status="running", started_at=datetime.now(UTC), error=None)
        progress: dict[str, Any] = {}

        def report_progress(values: dict[str, Any]) -> None:
            progress.update(values)

        async def flush() -> None:
            async with get_sessionmaker()() as other:
                await _set(other, run_id, progress=dict(progress))

        try:
            cells = grid_cells(settings) if params.sensitivity else [base_cell(settings)]
            tape_task = asyncio.create_task(
                ensure_tape(session, settings, params.start, params.end, cells, report_progress)
            )
            while not tape_task.done():
                await asyncio.wait({tape_task}, timeout=PROGRESS_SECONDS)
                if progress:
                    await flush()
            tape_row, tape, reused = tape_task.result()
            progress.update(stage="simulate")
            await flush()
            result = await simulate_run(params, tape, tape_row.cells, cells, settings)
            result["report"]["tape"].update(reused=reused, id=tape_row.id)
        except Exception as exc:
            log.exception("backtest.failed", run_id=run_id)
            await _set(
                session,
                run_id,
                status="failed",
                error=str(exc) or type(exc).__name__,
                finished_at=datetime.now(UTC),
            )
            raise
        report = result["report"]
        trades = report.pop("trades")
        await _set(
            session,
            run_id,
            status="done",
            tape_id=tape_row.id,
            summary=headline(report["summary"]),
            report=report,
            trades=trades,
            progress={"stage": "done"},
            finished_at=datetime.now(UTC),
        )
    stats = {
        "run_id": run_id,
        "trades": len(trades),
        "tape_reused": reused,
        "seconds": round(time.perf_counter() - started, 1),
        **headline(report["summary"]),
    }
    log.info("backtest.done", **stats)
    return stats


async def simulate_run(
    params: BacktestParams,
    tape: Tape,
    tape_cells: list[dict[str, float]],
    cells: list[Cell],
    settings: AppSettings,
) -> dict[str, Any]:
    """Stage 2 for the run's cell (and every grid cell when asked). `tape_cells` is the
    tape's own cell order (a reused tape may hold more cells, in another order)."""
    index = {(c["vcp"], c["volume"]): k for k, c in enumerate(tape_cells)}
    wanted = [index[(c.vcp_final_max_pct, c.breakout_volume_pct)] for c in cells]
    sessions = sessions_between(params.start, params.end)
    screen = params.rules.screen_filters
    used = cells if params.sensitivity else cells[:1]
    tickers = sorted(
        {int(t) for t in tape.candidates.filter(pl.col("cell") < len(used))["ticker_id"].unique()}
    )
    prices = await load_prices(tickers, sessions)
    matches = (lambda fields: screen_matches(fields, screen)) if screen else None
    sim = simulate(
        params,
        cell_candidates(tape, wanted[0], bool(screen)),
        cell_breakouts(tape, wanted[0]),
        prices,
        matches,
    )
    heatmap = None
    if params.sensitivity:
        grid: dict[tuple[float, float], dict[str, Any]] = {}
        for k, cell in zip(wanted, cells, strict=True):
            other = (
                sim
                if k == wanted[0]
                else simulate(
                    params,
                    cell_candidates(tape, k, bool(screen)),
                    cell_breakouts(tape, k),
                    prices,
                    matches,
                )
            )
            grid[(cell.vcp_final_max_pct, cell.breakout_volume_pct)] = heatmap_cell(other)
        vcps = [float(v) for v in settings.backtest_grid_vcp_final_pct]
        volumes = [float(v) for v in settings.backtest_grid_volume_pct]
        heatmap = {
            "vcp": vcps,
            "volume": volumes,
            "base": cells[0].as_dict(),
            "cells": [[grid.get((v, vol)) for vol in volumes] for v in vcps],
        }
    signals = (
        tape.signals.group_by("type").len().sort("type").iter_rows()
        if not tape.signals.is_empty()
        else []
    )
    base_rows = tape.candidates.filter(pl.col("cell") == wanted[0])
    report = build_report(
        params,
        sim,
        await benchmark_closes(sessions),
        tape={
            **tape.stats,
            "cells": len(cells),
            "candidates": base_rows.height,
            "setups": base_rows.select("ticker_id", "setup").unique().height,
        },
        signals={str(k): int(v) for k, v in signals},
        heatmap=heatmap,
    )
    return {"report": report}


async def backtest_job(trigger: Trigger, run_id: int) -> dict[str, Any]:
    async with track_job("backtest", trigger) as handle, job_lock(get_redis(), LOCK):
        stats = await run_backtest(run_id)
        handle.stats.update(stats)
        return stats


def clear_tapes() -> int:
    """Delete every cached tape file (they rebuild on demand). Returns the folders removed."""
    root = Path(get_settings().backtest_dir)
    if not root.exists():
        return 0
    removed = 0
    for child in root.glob("tape_*"):
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed

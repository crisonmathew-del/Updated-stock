"""Operator commands. Run with `python -m app.cli <command>` (or the matching `make` target).

create-user   Create the login user (prompts for the password, or reads BREAKOUT_PASSWORD)
set-password  Change a user's password
seed          Insert default settings that are missing (never overwrites changes)
universe      Rebuild the universe from the exchange directories (+ SEC reference data)
backfill      Load daily history for tickers that don't have it yet (resumable)
eod-update    Fetch the latest session's bars, then run the data-quality checks
data-quality  Run the data-quality checks only
scan          Recompute analytics (indicators, RS, groups, breadth, regime); --full for all history
fundamentals  Load statements, earnings dates and insider trades (nightly; --full for everyone)
patterns      Grades and pattern detection as of a date (--date), optionally for --symbols only
setups        Scores, lifecycle and signals for every session not processed yet (through --date)
outcomes      Update signal outcomes (returns after 1-60 sessions, stop/2R/+20% dates)
digests       Send the daily or weekly digest if one is due
replay        Play a recorded session through the intraday watcher (alerts, then the close)
export-recording  Write a stored session's minute bars as a recording (CSV, .gz to compress)
volume-curve  Learn the time-of-day volume curve from stored minute bars
backtest      Run a backtest of the default rules (--start/--end, --sensitivity for the grid)
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.alerts import jobs as alert_jobs
from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker
from app.core.jobs import JobAlreadyRunningError
from app.core.logging import configure_logging
from app.core.redis import get_redis
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    PasswordTooShortError,
    UserExistsError,
    create_user,
    set_password,
)
from app.data import jobs
from app.intraday.service import export_recording, run_replay, volume_curve_job
from app.providers.base import ProviderError
from app.settings import store

Command = Callable[[argparse.Namespace], Awaitable[int]]


def _read_password() -> str:
    from_env = os.environ.get("BREAKOUT_PASSWORD")
    if from_env:
        return from_env
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords don't match. Nothing was changed.")
    return first


async def cmd_create_user(args: argparse.Namespace) -> int:
    password = _read_password()
    async with get_sessionmaker()() as session:
        try:
            user = await create_user(session, args.email, password)
        except (UserExistsError, PasswordTooShortError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    print(f"Created user {user.email}. Sign in at /login.")
    return 0


async def cmd_set_password(args: argparse.Namespace) -> int:
    password = _read_password()
    async with get_sessionmaker()() as session:
        try:
            user = await set_password(session, args.email, password)
        except (LookupError, PasswordTooShortError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    print(f"Password updated for {user.email}.")
    return 0


async def cmd_seed(_: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        added = await store.seed(session)
    print(f"Seeded {len(added)} setting(s)." if added else "All settings already present.")
    return 0


def _print_stats(stats: dict[str, Any]) -> int:
    print(json.dumps(stats, indent=2, default=str))
    return 0


async def _run_job(job: Awaitable[dict[str, Any]]) -> int:
    try:
        return _print_stats(await job)
    except (JobAlreadyRunningError, ProviderError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


async def cmd_universe(args: argparse.Namespace) -> int:
    return await _run_job(jobs.universe_job("cli", then_backfill=args.then_backfill))


async def cmd_backfill(args: argparse.Namespace) -> int:
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    return await _run_job(
        jobs.backfill_job("cli", years=args.years, symbols=symbols, force=args.force)
    )


async def cmd_eod_update(args: argparse.Namespace) -> int:
    session_date = date.fromisoformat(args.date) if args.date else None
    return await _run_job(jobs.eod_update_job("cli", session_date=session_date))


async def cmd_data_quality(_: argparse.Namespace) -> int:
    return await _run_job(jobs.data_quality_job("cli"))


async def cmd_scan(args: argparse.Namespace) -> int:
    through = date.fromisoformat(args.date) if args.date else None
    return await _run_job(jobs.analytics_job("cli", through=through, force_full=args.full))


async def cmd_fundamentals(args: argparse.Namespace) -> int:
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    return await _run_job(jobs.fundamentals_job("cli", full=args.full, symbols=symbols))


async def cmd_patterns(args: argparse.Namespace) -> int:
    as_of = date.fromisoformat(args.date) if args.date else None
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    return await _run_job(jobs.patterns_job("cli", as_of=as_of, symbols=symbols))


async def cmd_setups(args: argparse.Namespace) -> int:
    through = date.fromisoformat(args.date) if args.date else None
    return await _run_job(jobs.setups_job("cli", through=through))


async def cmd_outcomes(_: argparse.Namespace) -> int:
    return await _run_job(jobs.outcomes_job("cli"))


async def cmd_digests(_: argparse.Namespace) -> int:
    return await _run_job(alert_jobs.digests_job("cli"))


async def cmd_replay(args: argparse.Namespace) -> int:
    try:
        stats = await run_replay(
            Path(args.file), speed=args.speed, start=args.start, close=not args.no_close
        )
    except ProviderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return _print_stats(stats)


async def cmd_export_recording(args: argparse.Namespace) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    count = await export_recording(date.fromisoformat(args.date), Path(args.out), symbols)
    if not count:
        print(f"No minute bars stored for {args.date}.", file=sys.stderr)
        return 1
    print(f"Wrote {count} minute bars to {args.out}.")
    return 0


async def cmd_volume_curve(_: argparse.Namespace) -> int:
    return await _run_job(volume_curve_job("cli"))


async def cmd_backtest(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from app.api.routes.backtests import _describe, default_start
    from app.backtest.engine import BacktestParams
    from app.backtest.jobs import backtest_job
    from app.models import BacktestRun, IndicatorDaily, User

    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).order_by(User.id).limit(1))
        if user is None:
            print("Error: create the login user first (make create-user).", file=sys.stderr)
            return 1
        first, last = (
            await session.execute(
                select(func.min(IndicatorDaily.date), func.max(IndicatorDaily.date))
            )
        ).one()
        if last is None:
            print("Error: no analytics yet: backfill prices and run the scan.", file=sys.stderr)
            return 1
        settings = await store.load(session)
        end = date.fromisoformat(args.end) if args.end else last
        start = (
            date.fromisoformat(args.start)
            if args.start
            else default_start(first, end, settings.backtest_years)
        )
        params = BacktestParams.defaults(settings, start, end).model_copy(
            update={"sensitivity": args.sensitivity}
        )
        run = BacktestRun(
            user_id=user.id,
            name=_describe(params),
            status="queued",
            params=params.model_dump(mode="json"),
            progress={"stage": "queued"},
        )
        session.add(run)
        await session.commit()
        run_id = run.id
    return await _run_job(backtest_job("cli", run_id))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="Create the login user")
    p.add_argument("--email", required=True)
    p.set_defaults(handler=cmd_create_user)

    p = sub.add_parser("set-password", help="Change a user's password")
    p.add_argument("--email", required=True)
    p.set_defaults(handler=cmd_set_password)

    p = sub.add_parser("seed", help="Insert missing default settings")
    p.set_defaults(handler=cmd_seed)

    p = sub.add_parser("universe", help="Rebuild the universe")
    p.add_argument("--then-backfill", action="store_true", help="Backfill new tickers after")
    p.set_defaults(handler=cmd_universe)

    p = sub.add_parser("backfill", help="Load daily history")
    p.add_argument(
        "--years", type=int, help="Years of history (default: the backfill_years setting)"
    )
    p.add_argument("--symbols", help="Comma-separated symbols (default: every pending ticker)")
    p.add_argument("--force", action="store_true", help="Re-fetch even if already loaded")
    p.set_defaults(handler=cmd_backfill)

    p = sub.add_parser("eod-update", help="Fetch the latest session and run quality checks")
    p.add_argument("--date", help="Session date YYYY-MM-DD (default: the latest closed session)")
    p.set_defaults(handler=cmd_eod_update)

    p = sub.add_parser("data-quality", help="Run the data-quality checks")
    p.set_defaults(handler=cmd_data_quality)

    p = sub.add_parser("scan", help="Recompute analytics from stored prices")
    p.add_argument("--date", help="Through this session YYYY-MM-DD (default: the latest)")
    p.add_argument("--full", action="store_true", help="Recompute all history")
    p.set_defaults(handler=cmd_scan)

    p = sub.add_parser("fundamentals", help="Load statements, earnings dates and insider trades")
    p.add_argument("--full", action="store_true", help="Refresh every company")
    p.add_argument("--symbols", help="Comma-separated symbols (refresh just these)")
    p.set_defaults(handler=cmd_fundamentals)

    p = sub.add_parser("patterns", help="Grades and pattern detection as of a date")
    p.add_argument("--date", help="As of this session YYYY-MM-DD (default: the latest)")
    p.add_argument("--symbols", help="Comma-separated symbols (default: the liquid universe)")
    p.set_defaults(handler=cmd_patterns)

    p = sub.add_parser("setups", help="Scores, lifecycle and signals for new sessions")
    p.add_argument("--date", help="Through this session YYYY-MM-DD (default: the latest)")
    p.set_defaults(handler=cmd_setups)

    p = sub.add_parser("digests", help="Send the daily or weekly digest if one is due")
    p.set_defaults(handler=cmd_digests)

    p = sub.add_parser("outcomes", help="Update signal outcomes")
    p.set_defaults(handler=cmd_outcomes)

    p = sub.add_parser("replay", help="Replay a recorded session through the intraday watcher")
    p.add_argument("--file", required=True, help="Recording (CSV of minute bars, or .csv.gz)")
    p.add_argument("--speed", type=float, default=60, help="× real time (0 = as fast as possible)")
    p.add_argument("--start", help="Skip quickly to this time (HH:MM US/Eastern)")
    p.add_argument("--no-close", action="store_true", help="Don't run the close at the end")
    p.set_defaults(handler=cmd_replay)

    p = sub.add_parser("export-recording", help="Write a stored session's minute bars to a file")
    p.add_argument("--date", required=True, help="Session YYYY-MM-DD")
    p.add_argument("--out", required=True, help="Output path (.csv or .csv.gz)")
    p.add_argument("--symbols", help="Comma-separated symbols (default: all stored)")
    p.set_defaults(handler=cmd_export_recording)

    p = sub.add_parser("volume-curve", help="Learn the time-of-day volume curve")
    p.set_defaults(handler=cmd_volume_curve)

    p = sub.add_parser("backtest", help="Run a backtest of the default rules")
    p.add_argument("--start", help="First session YYYY-MM-DD (default: backtest_years back)")
    p.add_argument("--end", help="Last session YYYY-MM-DD (default: the latest)")
    p.add_argument("--sensitivity", action="store_true", help="Also run the sensitivity grid")
    p.set_defaults(handler=cmd_backtest)
    return parser


async def _run(handler: Command, args: argparse.Namespace) -> int:
    try:
        return await handler(args)
    finally:
        await get_redis().aclose()
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, "console")
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args.handler, args))


if __name__ == "__main__":
    raise SystemExit(main())

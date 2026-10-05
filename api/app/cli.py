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
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

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

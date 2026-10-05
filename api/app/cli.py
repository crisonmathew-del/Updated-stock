"""Operator commands. Run with `python -m app.cli <command>` (or the matching `make` target).

create-user   Create the login user (prompts for the password, or reads BREAKOUT_PASSWORD)
set-password  Change a user's password
seed          Insert default settings that are missing (never overwrites changes)
"""

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Awaitable, Callable

from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker
from app.core.logging import configure_logging
from app.core.redis import get_redis
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    PasswordTooShortError,
    UserExistsError,
    create_user,
    set_password,
)
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

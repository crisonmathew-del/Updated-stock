"""Seed the database the web end-to-end tests run against (`web/e2e`, see playwright.config.ts).

`uv run python -m tests.e2e_seed` recreates the database named in DATABASE_URL, migrates it,
loads the drawn VCP breakout market from the API tests (SPOT broke out on session 333; AAPL,
the benchmarks and the sector ETFs follow a steady trend), runs the analytics and setups
stages, and creates the login user. It refuses any database whose name doesn't end in `_e2e`,
so it can never wipe development or production data, and it flushes only a Redis database
other than 0 (the default one the app uses).
"""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker
from app.core.redis import get_redis
from app.core.security import create_user
from tests.test_setups_api import breakout_market

API_ROOT = Path(__file__).resolve().parent.parent
EMAIL = os.environ.get("E2E_EMAIL", "owner@example.com")
PASSWORD = os.environ.get("E2E_PASSWORD", "e2e password 1234")


async def recreate(url: str) -> None:
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    await admin.dispose()


async def seed() -> None:
    async with get_sessionmaker()() as session:
        await breakout_market(session)
        await create_user(session, EMAIL, PASSWORD)
    # Sessions and cached screener snapshots from an earlier run point at the old database.
    await get_redis().flushdb()
    await get_redis().aclose()
    await get_engine().dispose()


def main() -> None:
    settings = get_settings()
    url = settings.database_url
    name = make_url(url).database or ""
    if not name.endswith("_e2e"):
        raise SystemExit(f'Refusing to seed "{name}": the e2e database name must end in "_e2e".')
    if (urlparse(settings.redis_url).path.strip("/") or "0") == "0":
        raise SystemExit("Refusing to flush Redis database 0: point REDIS_URL at another one.")
    asyncio.run(recreate(url))
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(config, "head")
    asyncio.run(seed())
    print(f"Seeded {name}: the drawn VCP breakout market and {EMAIL}.")


if __name__ == "__main__":
    main()

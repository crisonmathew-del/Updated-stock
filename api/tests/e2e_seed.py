"""Seed the database the web end-to-end tests run against (`web/e2e`, see playwright.config.ts).

`uv run python -m tests.e2e_seed` recreates the database named in DATABASE_URL, migrates it,
loads the drawn VCP breakout market from the API tests (SPOT broke out on session 333; AAPL,
the benchmarks and the sector ETFs follow a steady trend), runs the analytics and setups
stages, creates the login user and runs a short backtest over the last sessions (SPOT's
breakout is its one trade) for the backtest lab journey. It refuses any database whose name
doesn't end in `_e2e`, so it can never wipe development or production data, and it flushes
only a Redis database other than 0 (the default one the app uses).
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

from app.api.routes.backtests import _describe
from app.backtest.engine import BacktestParams
from app.backtest.jobs import run_backtest
from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker
from app.core.redis import get_redis
from app.core.security import create_user
from app.models import BacktestRun
from app.settings import store
from tests.test_detection_pipeline import DAYS
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
    # Sessions and cached screener snapshots from an earlier run point at the old database.
    await get_redis().flushdb()
    async with get_sessionmaker()() as session:
        await breakout_market(session)
        user = await create_user(session, EMAIL, PASSWORD)
        # Any grade, so SPOT's breakout (bought at the next open, held to the end) is traded.
        params = BacktestParams.defaults(await store.load(session), DAYS[300], DAYS[334])
        params.rules = params.rules.model_copy(update={"min_grade": None})
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
    result = await run_backtest(run_id)
    async with get_sessionmaker()() as session:
        done = await session.get(BacktestRun, run_id)
        if done is None or done.status != "done" or not done.trades:
            raise SystemExit(f"The seed backtest didn't finish with a trade: {result}")
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

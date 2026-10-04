"""Test configuration.

Tests never touch the development database or Redis keyspace: before the app is imported we
point DATABASE_URL at `<db>_test` and REDIS_URL at logical database 15 (override with
TEST_DATABASE_URL / TEST_REDIS_URL). Integration tests are marked `integration` and need real
Postgres + Redis; run `uv run pytest -m "not integration"` for a quick dependency-free pass.
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings, get_settings
from app.core.redis import get_redis

API_ROOT = Path(__file__).resolve().parent.parent


def _point_at_test_infrastructure() -> None:
    base = Settings()
    db_url = make_url(os.environ.get("TEST_DATABASE_URL") or base.database_url)
    if "TEST_DATABASE_URL" not in os.environ and not (db_url.database or "").endswith("_test"):
        db_url = db_url.set(database=f"{db_url.database}_test")
    redis_url = os.environ.get("TEST_REDIS_URL") or re.sub(r"/\d+$", "", base.redis_url) + "/15"

    os.environ["DATABASE_URL"] = db_url.render_as_string(hide_password=False)
    os.environ["REDIS_URL"] = redis_url
    os.environ["APP_ENV"] = "test"
    get_settings.cache_clear()


_point_at_test_infrastructure()


@pytest.fixture(scope="session")
async def migrated_db() -> None:
    """Create the test database if needed and migrate it to head."""
    url = make_url(get_settings().database_url)
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin.dispose()

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # env.py calls asyncio.run(), so migrate from a worker thread outside the test event loop.
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture
async def clean_redis() -> AsyncIterator[None]:
    redis = get_redis()
    await redis.flushdb()
    yield
    await redis.flushdb()

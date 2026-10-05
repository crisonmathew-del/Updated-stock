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

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings, get_settings
from app.core.db import Base, get_sessionmaker
from app.core.redis import get_redis
from app.core.security import create_user
from app.main import app as fastapi_app
from app.models import User

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


@pytest.fixture
async def db(migrated_db: None) -> AsyncIterator[AsyncSession]:
    """A session on an empty, migrated test database (every app table is truncated first)."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with get_sessionmaker()() as session:
        await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await session.commit()
        yield session


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client for the app. Sends the CSRF header that the web client always sends."""
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "breakout"}
    ) as client:
        yield client


TEST_EMAIL = "owner@example.com"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
async def user(db: AsyncSession, clean_redis: None) -> User:
    return await create_user(db, TEST_EMAIL, TEST_PASSWORD)


@pytest.fixture
async def signed_in(client: httpx.AsyncClient, user: User) -> httpx.AsyncClient:
    response = await client.post(
        "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client

"""Alembic migration environment (async). The database URL comes from app settings, never from
alembic.ini, so migrations always target the same database as the app."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import get_settings
from app.core.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# TimescaleDB creates its own internal schemas and tables; never autogenerate against them.
TIMESCALE_SCHEMAS = {
    "_timescaledb_catalog",
    "_timescaledb_config",
    "_timescaledb_internal",
    "_timescaledb_cache",
    "_timescaledb_functions",
    "timescaledb_information",
    "timescaledb_experimental",
    "toolkit_experimental",
}


def include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    if type_ == "schema":
        return name not in TIMESCALE_SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())

"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.core.security import SESSION_COOKIE, CurrentUser, resolve_session


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


def get_redis_client() -> Redis:
    return get_redis()


DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis_client)]


async def current_user(
    redis: RedisClient,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> CurrentUser:
    if session_token:
        user = await resolve_session(redis, session_token)
        if user is not None:
            return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")


AuthUser = Annotated[CurrentUser, Depends(current_user)]

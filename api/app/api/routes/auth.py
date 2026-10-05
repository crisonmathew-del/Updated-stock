"""Sign in, sign out, and who am I."""

import math

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import AuthUser, DbSession, RedisClient
from app.core.config import get_settings
from app.core.security import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    authenticate,
    clear_login_failures,
    create_session,
    destroy_session,
    login_blocked_for,
    record_login_failure,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    email: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, db: DbSession, redis: RedisClient
) -> UserResponse:
    ip = _client_ip(request)
    wait = await login_blocked_for(redis, body.email, ip)
    if wait:
        minutes = math.ceil(wait / 60)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed sign-in attempts. Try again in {minutes} minute"
            f"{'' if minutes == 1 else 's'}.",
            headers={"Retry-After": str(wait)},
        )

    user = await authenticate(db, body.email, body.password)
    if user is None:
        await record_login_failure(redis, body.email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect.")

    await clear_login_failures(redis, body.email, ip)
    token = await create_session(redis, user)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=get_settings().app_env == "prod",
        samesite="lax",
        path="/",
    )
    return UserResponse(email=user.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, redis: RedisClient) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await destroy_session(redis, token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserResponse)
async def me(user: AuthUser) -> UserResponse:
    return UserResponse(email=user.email)

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    MAX_FAILURES_PER_EMAIL,
    SESSION_COOKIE,
    PasswordTooShortError,
    UserExistsError,
    create_user,
    hash_password,
    verify_password,
)
from app.models import User
from tests.conftest import TEST_EMAIL, TEST_PASSWORD


def test_passwords_are_hashed_with_argon2_and_verified() -> None:
    hashed = hash_password("a long enough password")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a long enough password")
    assert not verify_password(hashed, "a different password")
    assert not verify_password("not-a-hash", "anything")


def test_short_passwords_are_rejected() -> None:
    with pytest.raises(PasswordTooShortError):
        hash_password("short")


@pytest.mark.integration
async def test_emails_are_normalised_and_unique(db: AsyncSession) -> None:
    user = await create_user(db, "  Owner@Example.COM ", TEST_PASSWORD)
    assert user.email == "owner@example.com"
    with pytest.raises(UserExistsError):
        await create_user(db, "owner@example.com", TEST_PASSWORD)


@pytest.mark.integration
async def test_login_sets_an_http_only_session_cookie(
    client: httpx.AsyncClient, user: User
) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json() == {"email": TEST_EMAIL}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    me = await client.get("/api/auth/me")
    assert me.json() == {"email": TEST_EMAIL}


@pytest.mark.integration
async def test_wrong_password_and_unknown_email_get_the_same_answer(
    client: httpx.AsyncClient, user: User
) -> None:
    wrong = await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "nope"})
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Email or password is incorrect."}


@pytest.mark.integration
async def test_repeated_failures_are_rate_limited(client: httpx.AsyncClient, user: User) -> None:
    for _ in range(MAX_FAILURES_PER_EMAIL):
        response = await client.post(
            "/api/auth/login", json={"email": TEST_EMAIL, "password": "bad"}
        )
        assert response.status_code == 401

    blocked = await client.post(
        "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )

    assert blocked.status_code == 429
    assert "Try again in 15 minutes" in blocked.json()["detail"]
    assert int(blocked.headers["retry-after"]) > 0


@pytest.mark.integration
async def test_logout_invalidates_the_session(signed_in: httpx.AsyncClient) -> None:
    token = signed_in.cookies[SESSION_COOKIE]

    assert (await signed_in.post("/api/auth/logout")).status_code == 204

    signed_in.cookies.set(SESSION_COOKIE, token)  # replaying the old cookie must not work
    assert (await signed_in.get("/api/auth/me")).status_code == 401


@pytest.mark.integration
@pytest.mark.usefixtures("clean_redis")
async def test_protected_routes_need_a_session(client: httpx.AsyncClient) -> None:
    for path in ("/api/auth/me", "/api/settings"):
        response = await client.get(path)
        assert response.status_code == 401, path
        assert response.json() == {"detail": "Sign in to continue."}

    client.cookies.set(SESSION_COOKIE, "forged-token")
    assert (await client.get("/api/settings")).status_code == 401


async def test_unsafe_requests_without_the_csrf_header_are_refused() -> None:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as bare:
        response = await bare.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "x"})
        health = await bare.get("/api/health")

    assert response.status_code == 403
    assert "x-requested-with" in response.json()["detail"]
    assert health.status_code == 200

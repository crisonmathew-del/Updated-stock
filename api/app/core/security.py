"""Single-user authentication: argon2 password hashes, opaque session tokens in Redis, and login
rate limiting.

The browser holds a random token in an HTTP-only cookie. Redis stores the session under an HMAC
of that token (keyed by SESSION_SECRET), so a Redis dump alone cannot be replayed as cookies.
"""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User

SESSION_COOKIE = "breakout_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
MIN_PASSWORD_LENGTH = 12

LOGIN_WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 20

_hasher = PasswordHasher()
# Verified against when the email is unknown, so response time doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str


class PasswordTooShortError(ValueError):
    pass


class UserExistsError(ValueError):
    pass


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def create_user(session: AsyncSession, email: str, password: str) -> User:
    email = normalize_email(email)
    exists = await session.scalar(select(func.count()).select_from(User).where(User.email == email))
    if exists:
        raise UserExistsError(f"A user with email {email} already exists")
    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    await session.commit()
    return user


async def set_password(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise LookupError(f"No user with email {email}")
    user.password_hash = hash_password(password)
    await session.commit()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return user


# --- Sessions -------------------------------------------------------------------------------


def _session_key(token: str) -> str:
    secret = get_settings().session_secret.get_secret_value().encode()
    digest = hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()
    return f"session:{digest}"


async def create_session(redis: Redis, user: User) -> str:
    token = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": user.id, "email": user.email})
    await redis.set(_session_key(token), payload, ex=SESSION_TTL_SECONDS)
    return token


async def resolve_session(redis: Redis, token: str) -> CurrentUser | None:
    """Return the session's user and slide its expiry forward, or None if invalid/expired."""
    key = _session_key(token)
    payload = await redis.get(key)
    if payload is None:
        return None
    await redis.expire(key, SESSION_TTL_SECONDS)
    data = json.loads(payload)
    return CurrentUser(id=int(data["user_id"]), email=str(data["email"]))


async def destroy_session(redis: Redis, token: str) -> None:
    await redis.delete(_session_key(token))


# --- Login rate limiting --------------------------------------------------------------------


def _attempt_keys(email: str, ip: str) -> tuple[str, str]:
    email_hash = hashlib.sha256(normalize_email(email).encode()).hexdigest()[:32]
    return f"login_failures:email:{email_hash}", f"login_failures:ip:{ip}"


async def login_blocked_for(redis: Redis, email: str, ip: str) -> int:
    """Seconds until another attempt is allowed, or 0 if the caller may try now."""
    email_key, ip_key = _attempt_keys(email, ip)
    email_count, ip_count = await redis.mget([email_key, ip_key])
    blocked = []
    if email_count is not None and int(email_count) >= MAX_FAILURES_PER_EMAIL:
        blocked.append(email_key)
    if ip_count is not None and int(ip_count) >= MAX_FAILURES_PER_IP:
        blocked.append(ip_key)
    if not blocked:
        return 0
    ttls = [int(await redis.ttl(key)) for key in blocked]
    return max(*ttls, 1)


async def record_login_failure(redis: Redis, email: str, ip: str) -> None:
    for key in _attempt_keys(email, ip):
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, LOGIN_WINDOW_SECONDS)


async def clear_login_failures(redis: Redis, email: str, ip: str) -> None:
    email_key, _ = _attempt_keys(email, ip)
    await redis.delete(email_key)

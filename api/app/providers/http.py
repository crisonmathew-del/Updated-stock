"""HTTP helper shared by the REST adapters: rate limiting, retries with exponential backoff
(honouring Retry-After), and clear errors."""

import asyncio
import random

import httpx

from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.providers.base import ProviderError, RateLimitedError

log = get_logger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def new_client(
    user_agent: str, timeout: float = 30.0, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", **(headers or {})},
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
    )


async def get(
    client: httpx.AsyncClient,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    max_attempts: int = 5,
    base_delay: float = 1.0,
) -> httpx.Response:
    """GET with retries. Returns 2xx and 404 responses; raises ProviderError otherwise."""
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        if limiter is not None:
            await limiter.acquire()
        try:
            response = await client.get(url)
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400 or response.status_code == 404:
                return response
            last_error = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUSES:
                raise ProviderError(f"GET {url} failed: {last_error}", status=response.status_code)

        if attempt == max_attempts:
            break
        delay = base_delay * 2 ** (attempt - 1) + random.uniform(0, base_delay / 2)
        if last_error == "HTTP 429":
            retry_after = response.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = max(delay, float(retry_after))
        log.warning("provider.retry", url=url, attempt=attempt, error=last_error, delay=delay)
        await asyncio.sleep(delay)

    if last_error == "HTTP 429":
        raise RateLimitedError(f"GET {url} is still rate-limited after {max_attempts} attempts")
    raise ProviderError(f"GET {url} failed after {max_attempts} attempts: {last_error}")

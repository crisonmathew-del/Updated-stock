import httpx
import pytest

from app.providers import http
from app.providers.base import ProviderError, RateLimitedError


def client_returning(*statuses: int) -> tuple[httpx.AsyncClient, list[int]]:
    calls: list[int] = []
    queue = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        status = queue.pop(0)
        calls.append(status)
        return httpx.Response(status, json={"ok": status == 200})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_retries_server_errors_then_succeeds() -> None:
    client, calls = client_returning(503, 502, 200)
    response = await http.get(client, "https://example.test/x", base_delay=0.001)
    assert response.status_code == 200
    assert calls == [503, 502, 200]


async def test_not_found_is_returned_not_retried() -> None:
    client, calls = client_returning(404)
    response = await http.get(client, "https://example.test/x", base_delay=0.001)
    assert response.status_code == 404
    assert calls == [404]


async def test_client_errors_fail_fast() -> None:
    client, calls = client_returning(403)
    with pytest.raises(ProviderError, match="HTTP 403") as caught:
        await http.get(client, "https://example.test/x", base_delay=0.001)
    assert calls == [403]
    assert caught.value.status == 403


async def test_persistent_throttling_raises_rate_limited() -> None:
    client, calls = client_returning(429, 429, 429)
    with pytest.raises(RateLimitedError):
        await http.get(client, "https://example.test/x", max_attempts=3, base_delay=0.001)
    assert len(calls) == 3

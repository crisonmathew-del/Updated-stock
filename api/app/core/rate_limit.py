"""Token-bucket rate limiter shared by every process through Redis.

Each provider gets one bucket (e.g. `ratelimit:sec`), so the worker, the scheduler and a CLI
backfill running at the same time still respect the provider's limit together. The bucket logic
runs as a Lua script using Redis server time, so it is atomic and immune to clock skew between
containers.
"""

import asyncio

from redis.asyncio import Redis

_TOKEN_BUCKET = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + (now - ts) / 1000 * rate)

local wait_ms = 0
if tokens >= cost then
  tokens = tokens - cost
else
  wait_ms = math.ceil((cost - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 1000) + 1000)
return wait_ms
"""


class RateLimiter:
    """`await limiter.acquire()` returns once a token is available."""

    def __init__(self, redis: Redis, name: str, per_second: float, burst: float | None = None):
        if per_second <= 0:
            raise ValueError("per_second must be positive")
        self._redis = redis
        self._key = f"ratelimit:{name}"
        self._rate = per_second
        self._capacity = burst if burst is not None else max(1.0, per_second)
        self._script = redis.register_script(_TOKEN_BUCKET)

    async def acquire(self, cost: float = 1.0) -> None:
        while True:
            wait_ms = int(
                await self._script(keys=[self._key], args=[self._capacity, self._rate, cost])
            )
            if wait_ms <= 0:
                return
            await asyncio.sleep(wait_ms / 1000)

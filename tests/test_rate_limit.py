import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.rate_limit import TokenBucketLimiter
from app.schemas.api_key import Scope
from tests.conftest import ClientFactory


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(get_settings().redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def strict_limiter(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> TokenBucketLimiter:
    """Burst of 3, refilling one token every 100 seconds."""
    limiter = TokenBucketLimiter(redis, rate=0.01, burst=3)
    monkeypatch.setattr("app.api.auth.get_rate_limiter", lambda: limiter)
    return limiter


async def test_returns_429_with_retry_after_once_burst_is_spent(
    strict_limiter: TokenBucketLimiter, client: AsyncClient
) -> None:
    statuses = [(await client.get("/v1/webhook-endpoints")).status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]
    limited = await client.get("/v1/webhook-endpoints")
    assert limited.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 100


async def test_limits_are_per_api_key(
    strict_limiter: TokenBucketLimiter, make_client: ClientFactory
) -> None:
    noisy = await make_client(Scope.ADMIN)
    quiet = await make_client(Scope.ADMIN)

    for _ in range(3):
        await noisy.get("/v1/webhook-endpoints")

    assert (await noisy.get("/v1/webhook-endpoints")).status_code == 429
    assert (await quiet.get("/v1/webhook-endpoints")).status_code == 200


async def test_unauthenticated_requests_do_not_spend_tokens(
    strict_limiter: TokenBucketLimiter, redis: Redis, make_client: ClientFactory
) -> None:
    anonymous = await make_client()

    await anonymous.get("/v1/webhook-endpoints")

    assert await redis.keys("ratelimit:*") == []


async def test_bucket_refills_over_time(redis: Redis) -> None:
    limiter = TokenBucketLimiter(redis, rate=20, burst=2)  # one token per 50ms

    results = [await limiter.hit("k") for _ in range(3)]
    assert [r.allowed for r in results] == [True, True, False]
    assert 0 < results[2].retry_after_seconds <= 0.05

    await asyncio.sleep(0.12)
    assert (await limiter.hit("k")).allowed


async def test_concurrent_hits_never_exceed_the_burst(redis: Redis) -> None:
    # The Lua script is atomic: 50 simultaneous requests can't all read
    # "tokens left" before any of them writes.
    limiter = TokenBucketLimiter(redis, rate=0.001, burst=5)

    results = await asyncio.gather(*(limiter.hit("hot") for _ in range(50)))

    assert sum(r.allowed for r in results) == 5


async def test_fails_open_when_redis_is_unreachable() -> None:
    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    limiter = TokenBucketLimiter(dead, rate=1, burst=1)

    results = [await limiter.hit("k") for _ in range(3)]

    assert all(r.allowed for r in results)
    await dead.aclose()

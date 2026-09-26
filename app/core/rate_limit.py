"""Per-API-key rate limiting with a token bucket in Redis.

Each key has a bucket holding up to `burst` tokens that refills at `rate` per
second. Each request spends one token; an empty bucket means 429. This allows
short bursts while capping the sustained rate.

The whole read-refill-spend-write cycle runs as ONE Lua script, which Redis
executes atomically. Doing it as separate GET/SET commands from Python would
be a race: two API replicas could both read "1 token left" and both spend it.
The script also uses Redis's own clock (TIME), so API replicas with slightly
different system clocks can't disagree about refills.
"""

from dataclasses import dataclass
from functools import lru_cache

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.metrics import RATE_LIMITER_ERRORS

logger = structlog.get_logger()

# KEYS[1] = bucket key; ARGV = burst, refill rate (tokens/second), cost.
# Returns {allowed (0/1), tokens remaining (floored), retry after (ms)}.
TOKEN_BUCKET_LUA = """
local burst = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)

local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(bucket[1]) or burst
local last_ms = tonumber(bucket[2]) or now_ms

tokens = math.min(burst, tokens + (now_ms - last_ms) / 1000 * rate)

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
    allowed = 1
    tokens = tokens - cost
else
    retry_after_ms = math.ceil((cost - tokens) / rate * 1000)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now_ms)
-- An idle bucket is full again after burst/rate seconds; expire it then.
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate * 1000) + 1000)
return {allowed, math.floor(tokens), retry_after_ms}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float


class TokenBucketLimiter:
    def __init__(self, redis: Redis, *, rate: float, burst: int, enabled: bool = True) -> None:
        self.redis = redis
        self.rate = rate
        self.burst = burst
        self.enabled = enabled
        self._script = redis.register_script(TOKEN_BUCKET_LUA)

    async def hit(self, key: str) -> RateLimitResult:
        if not self.enabled:
            return RateLimitResult(True, self.burst, self.burst, 0.0)
        try:
            allowed, remaining, retry_after_ms = await self._script(
                keys=[f"ratelimit:{key}"], args=[self.burst, self.rate, 1]
            )
        except RedisError as exc:
            # Fail open: if Redis is down, serve the request rather than turn a
            # rate-limiter outage into a full API outage. Alert on the metric.
            RATE_LIMITER_ERRORS.inc()
            logger.warning("rate_limiter_unavailable", error=str(exc))
            return RateLimitResult(True, self.burst, self.burst, 0.0)
        return RateLimitResult(bool(allowed), self.burst, int(remaining), retry_after_ms / 1000)

    async def close(self) -> None:
        await self.redis.aclose()


@lru_cache
def get_rate_limiter() -> TokenBucketLimiter:
    settings = get_settings()
    redis = Redis.from_url(
        settings.redis_url,
        # Short timeouts: a slow or hung Redis must not add latency to every
        # request. Past the timeout we fail open.
        socket_timeout=0.25,
        socket_connect_timeout=0.25,
    )
    return TokenBucketLimiter(
        redis,
        rate=settings.rate_limit_per_second,
        burst=settings.rate_limit_burst,
        enabled=settings.rate_limit_enabled,
    )

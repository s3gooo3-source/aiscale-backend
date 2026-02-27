"""
Rate limiter with Redis backend and automatic in-memory fallback.

Usage:
    await rate_limiter.check("user:{uid}", limit=10, window_secs=60)
    await rate_limiter.check("ip:{ip}",   limit=5,  window_secs=60)

Raises HTTP 429 if limit exceeded.
"""
import time
import logging
from collections import defaultdict
from typing import Dict, Tuple
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """Sliding-window in-memory rate limiter. Not shared across workers."""

    def __init__(self):
        # key -> list of timestamps
        self._windows: Dict[str, list] = defaultdict(list)

    async def check(self, key: str, limit: int, window_secs: int) -> None:
        now = time.time()
        cutoff = now - window_secs
        timestamps = self._windows[key]

        # Remove expired entries
        self._windows[key] = [t for t in timestamps if t > cutoff]

        if len(self._windows[key]) >= limit:
            retry_after = int(window_secs - (now - self._windows[key][0])) + 1
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limited",
                    "message": f"Too many requests. Try again in {retry_after} seconds.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        self._windows[key].append(now)

    async def get_count(self, key: str, window_secs: int) -> int:
        now = time.time()
        cutoff = now - window_secs
        self._windows[key] = [t for t in self._windows[key] if t > cutoff]
        return len(self._windows[key])


class RedisRateLimiter:
    """Sliding-window rate limiter using Redis sorted sets."""

    def __init__(self, redis_url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def check(self, key: str, limit: int, window_secs: int) -> None:
        import redis.asyncio as aioredis
        now = time.time()
        cutoff = now - window_secs
        full_key = f"rl:{key}"

        try:
            pipe = self._redis.pipeline()
            # Remove expired entries from sorted set
            pipe.zremrangebyscore(full_key, "-inf", cutoff)
            # Count current entries
            pipe.zcard(full_key)
            # Add current request
            pipe.zadd(full_key, {str(now): now})
            # Expire the key after window
            pipe.expire(full_key, window_secs + 1)
            results = await pipe.execute()

            current_count = results[1]  # after removal, before add
            if current_count >= limit:
                # Remove the entry we just added
                await self._redis.zrem(full_key, str(now))
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limited",
                        "message": f"Too many requests. Limit: {limit} per {window_secs}s.",
                        "retry_after_seconds": window_secs,
                    },
                    headers={"Retry-After": str(window_secs)},
                )
        except HTTPException:
            raise
        except Exception as e:
            # Redis failure → allow request (fail open), log warning
            logger.warning(f"Redis rate limit check failed: {e} — allowing request")

    async def get_count(self, key: str, window_secs: int) -> int:
        try:
            now = time.time()
            cutoff = now - window_secs
            await self._redis.zremrangebyscore(f"rl:{key}", "-inf", cutoff)
            return await self._redis.zcard(f"rl:{key}")
        except Exception:
            return 0


def build_rate_limiter(redis_url: str | None):
    if redis_url:
        try:
            limiter = RedisRateLimiter(redis_url)
            logger.info("Rate limiter: Redis backend")
            return limiter
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}), falling back to in-memory rate limiter")
    logger.info("Rate limiter: in-memory (not shared across workers)")
    return InMemoryRateLimiter()


# Singleton — initialised once at startup
from app.core.config import settings
rate_limiter = build_rate_limiter(settings.REDIS_URL)

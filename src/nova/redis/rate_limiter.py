"""
Distributed Rate Limiter using Redis Sliding Window algorithm.
Uses atomic Lua scripts to prevent race conditions under high concurrency.
"""

from __future__ import annotations

import time
import uuid

from nova.core.exceptions import NovaRateLimitError

# Lua script executed atomically inside Redis
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local request_id = ARGV[4]

-- 1. Remove outdated requests outside the current window
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

-- 2. Count requests in the current window
local count = redis.call('ZCARD', key)

-- 3. Check limit
if count < limit then
    -- Add new request with its timestamp as score
    redis.call('ZADD', key, now, request_id)
    -- Auto-expire the key to save memory if no requests come
    redis.call('EXPIRE', key, window)
    return 1 -- ALLOWED
else
    return 0 -- REJECTED
end
"""


async def async_check_rate_limit(
        key: str,
        limit: int,
        window_secs: int,
) -> bool:
    """
    Async rate limit check using the unified async Redis client.
    Returns True if allowed, raises NovaRateLimitError if rejected.
    """
    from nova.redis.client import get_async_redis_client

    client = get_async_redis_client()
    full_key = f"nova:rl:{key}"
    now = time.time()
    request_id = str(uuid.uuid4())

    # eval returns an integer (1 or 0)
    allowed = await client.eval(
        _SLIDING_WINDOW_LUA,
        1,  # Number of keys
        full_key,  # KEY[1]
        window_secs,  # ARGV[1]
        limit,  # ARGV[2]
        now,  # ARGV[3]
        request_id  # ARGV[4]
    )

    if not allowed:
        raise NovaRateLimitError(
            f"Rate limit exceeded for '{key}'",
            limit=limit,
            window_secs=window_secs
        )

    return True


def check_rate_limit(
        key: str,
        limit: int,
        window_secs: int,
) -> bool:
    """
    Sync rate limit check using the unified sync Redis client.
    Useful in WSGI middleware or synchronous tasks.
    """
    from nova.redis.client import get_redis_client

    client = get_redis_client()
    full_key = f"nova:rl:{key}"
    now = time.time()
    request_id = str(uuid.uuid4())

    allowed = client.eval(
        _SLIDING_WINDOW_LUA, 1, full_key, window_secs, limit, now, request_id
    )

    if not allowed:
        raise NovaRateLimitError(
            f"Rate limit exceeded for '{key}'",
            limit=limit,
            window_secs=window_secs
        )

    return True
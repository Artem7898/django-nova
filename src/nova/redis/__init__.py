"""Unified Redis Infrastructure: Client, Locks, Rate Limiter, Pub/Sub."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AsyncDistributedLock",
    "AsyncNovaPubSub",
    "RedisHealthReport",
    "async_check_rate_limit",
    "async_lock",
    "check_async_redis_health",
    "check_rate_limit",
    "check_redis_health",
    "close_redis_clients",
    "get_async_redis_client",
    "get_redis_client",
]

def __getattr__(name: str):
    if name == "get_redis_client":
        from nova.redis.client import get_redis_client
        return get_redis_client
    if name == "get_async_redis_client":
        from nova.redis.client import get_async_redis_client
        return get_async_redis_client
    if name == "close_redis_clients":
        from nova.redis.client import close_redis_clients
        return close_redis_clients
    if name == "AsyncDistributedLock":
        from nova.redis.locks import AsyncDistributedLock
        return AsyncDistributedLock
    if name == "async_lock":
        from nova.redis.locks import async_lock
        return async_lock
    if name == "RedisHealthReport":
        from nova.redis.health import RedisHealthReport
        return RedisHealthReport
    if name == "check_redis_health":
        from nova.redis.health import check_redis_health
        return check_redis_health
    if name == "check_async_redis_health":
        from nova.redis.health import check_async_redis_health
        return check_async_redis_health
    # New Stage 19 exports
    if name == "check_rate_limit":
        from nova.redis.rate_limiter import check_rate_limit
        return check_rate_limit
    if name == "async_check_rate_limit":
        from nova.redis.rate_limiter import async_check_rate_limit
        return async_check_rate_limit
    if name == "AsyncNovaPubSub":
        from nova.redis.pubsub import AsyncNovaPubSub
        return AsyncNovaPubSub

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.redis.client import close_redis_clients, get_async_redis_client, get_redis_client
    from nova.redis.health import RedisHealthReport, check_async_redis_health, check_redis_health
    from nova.redis.locks import AsyncDistributedLock, async_lock
    from nova.redis.pubsub import AsyncNovaPubSub
    from nova.redis.rate_limiter import async_check_rate_limit, check_rate_limit
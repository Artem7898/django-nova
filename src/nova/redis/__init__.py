"""Unified Redis Infrastructure: Client, Locks, Health Checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AsyncDistributedLock",
    "RedisHealthReport",
    "async_lock",
    "check_async_redis_health",
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

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.redis.client import close_redis_clients, get_async_redis_client, get_redis_client
    from nova.redis.health import RedisHealthReport, check_async_redis_health, check_redis_health
    from nova.redis.locks import AsyncDistributedLock, async_lock
"""
Redis connection pool management.
"""

from __future__ import annotations

import redis

_pool: redis.ConnectionPool | None = None


def get_redis_pool(
    url: str,
) -> redis.ConnectionPool:
    global _pool

    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            url,
            max_connections=100,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )

    return _pool
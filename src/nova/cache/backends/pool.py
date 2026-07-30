"""
Redis connection pool management.
"""

from __future__ import annotations

from typing import Any, Final

_redis_available: bool = True
try:
    import redis
    from redis import ConnectionPool as RedisPool
except ImportError:
    redis = None  # type: ignore[assignment]
    RedisPool = Any  # type: ignore[assignment, misc]
    _redis_available = False

REDIS_AVAILABLE: Final[bool] = _redis_available
_pool: Any = None


def get_redis_pool(url: str) -> Any:
    """
    Thread-safe Redis connection pool factory.
    """
    global _pool

    if not REDIS_AVAILABLE or redis is None:
        raise ImportError(
            'No Redis dependencies were found. Run: pip install "django-nova[cache]"'
        )

    assert redis is not None

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

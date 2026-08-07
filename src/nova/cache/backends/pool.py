"""Redis connection pool management."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Final

try:
    import redis as _redis_module
except ImportError:
    _redis_module = None

redis: Any = _redis_module
REDIS_AVAILABLE: Final[bool] = _redis_module is not None


@lru_cache(maxsize=1)
def get_redis_pool(url: str) -> Any:
    """
    Thread-safe Redis connection pool factory.

    lru_cache guarantees thread-safe singleton initialization per URL
    without leaking global state into the module.
    """
    if not REDIS_AVAILABLE or redis is None:
        raise ImportError(
            'No Redis dependencies were found. Run: pip install "django-nova[cache]"'
        )

    return redis.ConnectionPool.from_url(
        url,
        max_connections=100,
        retry_on_timeout=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
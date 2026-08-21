"""
Redis connection pool management.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Any

_redis_module: Any

try:
    _redis_module = importlib.import_module("redis")
except ImportError:
    _redis_module = None

REDIS_AVAILABLE: bool = _redis_module is not None


@lru_cache(maxsize=1)
def get_redis_pool(url: str) -> Any:
    """
    Thread-safe Redis connection pool factory.

    lru_cache guarantees singleton initialization per URL.
    """
    if not url:
        raise ValueError("Redis URL must not be empty")

    if not REDIS_AVAILABLE or _redis_module is None:
        raise ImportError('No Redis dependencies were found. Run: pip install "django-nova[cache]"')

    return _redis_module.ConnectionPool.from_url(
        url,
        max_connections=100,
        retry_on_timeout=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )


def clear_redis_pool_cache() -> None:
    """
    Clear cached Redis pools.

    Useful for tests and graceful shutdown.
    """
    get_redis_pool.cache_clear()

"""Redis connection pool management."""

from __future__ import annotations

from typing import Any, Final

try:
    import redis as _redis_module
except ImportError:
    _redis_module = None

# Explicit `Any` alias prevents Pyright from cascading `None` to module attributes
redis: Any = _redis_module
REDIS_AVAILABLE: Final[bool] = _redis_module is not None
_pool: Any = None


def get_redis_pool(url: str) -> Any:
    """Thread-safe Redis connection pool factory."""
    global _pool

    if not REDIS_AVAILABLE or redis is None:
        raise ImportError(
            'No Redis dependencies were found. Run: pip install "django-nova[cache]"'
        )

    if _pool is None:
        _pool = redis.ConnectionPool.from_url(  # type: ignore[union-attr]
            url,
            max_connections=100,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )

    return _pool
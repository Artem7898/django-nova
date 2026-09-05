"""
Unified Redis Client Infrastructure.

Provides process-wide connection pools for both Sync and Async contexts.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from nova.conf import nova_settings
from nova.core.exceptions import NovaCacheError

logger = logging.getLogger(__name__)

_sync_client: Any = None
_async_client: Any = None

try:
    import redis as _redis_module
except ImportError:
    _redis_module = None

try:
    import redis.asyncio as _aioredis_module
except ImportError:
    _aioredis_module = None

# Explicit Any aliases prevent Pyright from cascading None to module attributes.
redis: Any = _redis_module
aioredis: Any = _aioredis_module


def _get_sync_pool() -> Any:
    """Create a synchronous Redis connection pool from Nova settings."""
    if redis is None:
        raise NovaCacheError(
            "Redis package is not installed. Install it via: pip install 'django-nova[redis]'"
        )

    return redis.ConnectionPool.from_url(
        nova_settings.redis_url,
        max_connections=nova_settings.redis_max_connections,
        socket_timeout=nova_settings.redis_socket_timeout,
        socket_connect_timeout=nova_settings.redis_socket_connect_timeout,
        retry_on_timeout=nova_settings.redis_retry_on_timeout,
        health_check_interval=nova_settings.redis_health_check_interval,
    )


def _get_async_pool() -> Any:
    """Create an asynchronous Redis connection pool from Nova settings."""
    if aioredis is None:
        raise NovaCacheError(
            "Async redis package is not installed. Install it via: pip install 'django-nova[redis]'"
        )

    return aioredis.ConnectionPool.from_url(
        nova_settings.redis_url,
        max_connections=nova_settings.redis_max_connections,
        socket_timeout=nova_settings.redis_socket_timeout,
        socket_connect_timeout=nova_settings.redis_socket_connect_timeout,
        retry_on_timeout=nova_settings.redis_retry_on_timeout,
    )


def get_redis_client() -> Any:
    """Return the process-wide singleton synchronous Redis client."""
    global _sync_client

    if _sync_client is not None:
        return _sync_client

    if redis is None:
        raise NovaCacheError(
            "Redis package is not installed. Install it via: pip install 'django-nova[redis]'"
        )

    logger.info(
        "Initializing global Sync Redis client for URL: %s",
        nova_settings.redis_url,
    )

    pool = _get_sync_pool()
    _sync_client = redis.Redis(connection_pool=pool)

    return _sync_client


def get_async_redis_client() -> Any:
    """Return the process-wide singleton asynchronous Redis client."""
    global _async_client

    if _async_client is not None:
        return _async_client

    if aioredis is None:
        raise NovaCacheError(
            "Async redis package is not installed. Install it via: pip install 'django-nova[redis]'"
        )

    logger.info(
        "Initializing global Async Redis client for URL: %s",
        nova_settings.redis_url,
    )

    pool = _get_async_pool()
    _async_client = aioredis.Redis(connection_pool=pool)

    return _async_client


def set_sync_redis_client(client: Any | None) -> None:
    """
    Override the global sync Redis client.

    Intended for tests, health checks, and controlled shutdown.
    """
    global _sync_client
    _sync_client = client


def set_async_redis_client(client: Any | None) -> None:
    """
    Override the global async Redis client.

    Intended for tests, health checks, and controlled shutdown.
    """
    global _async_client
    _async_client = client


def reset_redis_clients() -> None:
    """Reset both process-wide Redis client references."""
    set_sync_redis_client(None)
    set_async_redis_client(None)


def close_redis_clients() -> None:
    """
    Gracefully close the global synchronous Redis client.

    Async clients are intentionally not closed here. Use
    ``aclose_redis_clients()`` from an async lifecycle boundary.

    The function is idempotent and isolates synchronous close failures
    so application shutdown is not interrupted by Redis cleanup errors.
    """
    global _sync_client

    client = _sync_client

    if client is None:
        return

    try:
        close_method = getattr(client, "close", None)

        if close_method is None:
            logger.warning("Sync Redis client does not provide a close() method.")
            return

        close_method()

        logger.info("Sync Redis client connection pool closed.")
    except Exception as exc:
        logger.error(
            "Error closing Sync Redis client: %s",
            exc,
        )
    finally:
        _sync_client = None


async def aclose_redis_clients() -> None:
    """
    Gracefully close the global asynchronous Redis client.

    The client reference is cleared only after a successful close.
    If closing fails, the reference is preserved so callers can retry
    cleanup or perform explicit recovery.
    """
    global _async_client

    client = _async_client

    if client is None:
        return

    close_method = getattr(client, "aclose", None)

    if close_method is None:
        logger.warning("Async Redis client does not provide an aclose() method.")
        return

    try:
        result = close_method()

        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("Error closing Async Redis client.")
        raise
    else:
        _async_client = None
        logger.info("Async Redis client connection pool closed.")

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

# Explicit `Any` alias prevents Pyright from cascading `None` to module attributes.
redis: Any = _redis_module
aioredis: Any = _aioredis_module


def _get_sync_pool() -> Any:
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
    """
    Returns the global singleton synchronous Redis client.
    """
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
    """
    Returns the global singleton asynchronous Redis client.
    """
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
    """
    Reset both global Redis clients.
    """
    set_sync_redis_client(None)
    set_async_redis_client(None)


def close_redis_clients() -> None:
    """
    Gracefully closes global sync connection pools.

    Async clients should be closed via aclose_redis_clients().
    """
    global _sync_client, _async_client

    for client_name, client in [("Sync", _sync_client), ("Async", _async_client)]:
        if client is None:
            continue

        try:
            close_method = getattr(client, "aclose", client.close)

            if inspect.iscoroutinefunction(close_method):
                logger.warning(
                    "%s Redis client requires async closing. Use aclose_redis_clients().",
                    client_name,
                )
            else:
                close_method()

            logger.info("%s Redis client connection pool closed.", client_name)
        except Exception as e:
            logger.error("Error closing %s Redis client: %s", client_name, e)

    _sync_client = None
    _async_client = None


async def aclose_redis_clients() -> None:
    """
    Gracefully closes async Redis clients.
    """
    global _async_client

    if _async_client is None:
        return

    close_method = getattr(_async_client, "aclose", None)

    if close_method is not None:
        result = close_method()

        if inspect.isawaitable(result):
            await result

    _async_client = None

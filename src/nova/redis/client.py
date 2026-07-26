"""
Unified Redis Client Infrastructure.
Provides process-wide connection pools for both Sync and Async contexts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nova.conf import nova_settings
from nova.core.exceptions import NovaCacheError

logger = logging.getLogger(__name__)

_sync_client: redis.Redis | None = None
_async_client: redis.asyncio.Redis | None = None

try:
    import redis
except ImportError:
    redis = None  # type: ignore

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore


def _get_sync_pool() -> redis.ConnectionPool:
    if redis is None:
        raise NovaCacheError("Redis package is not installed. Install it via: pip install 'django-nova[redis]'")

    return redis.ConnectionPool.from_url(
        nova_settings.redis_url,
        max_connections=nova_settings.redis_max_connections,
        socket_timeout=nova_settings.redis_socket_timeout,
        socket_connect_timeout=nova_settings.redis_socket_connect_timeout,
        retry_on_timeout=nova_settings.redis_retry_on_timeout,
        health_check_interval=nova_settings.redis_health_check_interval,
    )


def get_redis_client() -> redis.Redis:
    """Returns the global singleton synchronous Redis client."""
    global _sync_client

    if _sync_client is not None:
        return _sync_client

    logger.info("Initializing global Sync Redis client for URL: %s", nova_settings.redis_url)
    pool = _get_sync_pool()
    _sync_client = redis.Redis(connection_pool=pool)
    return _sync_client


def _get_async_pool() -> aioredis.ConnectionPool:
    if aioredis is None:
        raise NovaCacheError("Async redis package is not installed. Install it via: pip install 'django-nova[redis]'")

    return aioredis.ConnectionPool.from_url(
        nova_settings.redis_url,
        max_connections=nova_settings.redis_max_connections,
        socket_timeout=nova_settings.redis_socket_timeout,
        socket_connect_timeout=nova_settings.redis_socket_connect_timeout,
        retry_on_timeout=nova_settings.redis_retry_on_timeout,
    )


def get_async_redis_client() -> aioredis.Redis:
    """Returns the global singleton asynchronous Redis client."""
    global _async_client

    if _async_client is not None:
        return _async_client

    logger.info("Initializing global Async Redis client for URL: %s", nova_settings.redis_url)
    pool = _get_async_pool()
    _async_client = aioredis.Redis(connection_pool=pool)
    return _async_client


def close_redis_clients() -> None:
    """Gracefully closes global connection pools."""
    global _sync_client, _async_client

    for client_name, client in [("Sync", _sync_client), ("Async", _async_client)]:
        if client is not None:
            try:
                close_method = getattr(client, "aclose", client.close)
                import asyncio
                if asyncio.iscoroutinefunction(close_method):
                    logger.warning(f"{client_name} Redis client requires async closing, relying on GC.")
                else:
                    close_method()
                logger.info(f"{client_name} Redis client connection pool closed.")
            except Exception as e:
                logger.error(f"Error closing {client_name} Redis client: %s", e)

    _sync_client = None
    _async_client = None

if TYPE_CHECKING:
    import redis.asyncio as aioredis
"""
Unified Redis Client Infrastructure.

Provides a single, process-wide connection pool and Redis client instance.
All Nova modules (Cache, Tasks, Locks) must use this client to avoid connection sprawl.
"""

from __future__ import annotations

import logging

try:
    import redis
except ImportError:
    redis = None

from nova.conf import nova_settings
from nova.core.exceptions import NovaCacheError

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """
    Returns the global singleton Redis client.
    Initializes the connection pool on the first call based on nova_settings.redis_url.
    """
    global _client

    if _client is not None:
        return _client

    if redis is None:
        raise NovaCacheError(
            "Redis package is not installed. Install it via: pip install 'django-nova[cache]'"
        )

    try:
        url = nova_settings.redis_url
        logger.info("Initializing global Redis client for URL: %s", url)

        # ConnectionPool.from_url automatically parses the URL and creates a pool
        pool = redis.ConnectionPool.from_url(url, max_connections=20)
        _client = redis.Redis(connection_pool=pool)

        return _client
    except Exception as e:
        raise NovaCacheError(f"Failed to initialize global Redis client: {e}") from e


def close_redis_client() -> None:
    """
    Gracefully closes the global connection pool.
    Should be called on application shutdown.
    """
    global _client
    if _client is not None:
        try:
            _client.close()
            logger.info("Global Redis client connection pool closed.")
        except Exception as e:
            logger.error("Error while closing Redis client: %s", e)
        finally:
            _client = None
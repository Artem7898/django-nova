"""
Redis cache backend.

Requires:
    pip install "django-nova[cache]"
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = Exception  # type: ignore[assignment, misc]

from nova.cache.backends.protocol import CacheBackend
from nova.cache.serializers import PickleSerializer
from nova.core.exceptions import NovaCacheError

logger = logging.getLogger(__name__)


class RedisCacheBackend(CacheBackend):
    """
    Production-ready Redis cache backend.
    Uses the unified global Nova Redis Client to share connection pools.
    """

    def __init__(
        self,
        url: str | None = None,  # Left for backward compatibility, but ignored
        key_prefix: str = "nova",
    ) -> None:
        # We import here so as not to break the import if redis is not installed.
        from nova.redis.client import get_redis_client

        if url is not None:
            logger.warning(
                "Passing 'url' to RedisCacheBackend is deprecated. "
                "Nova now uses a unified Redis client configured via NOVA_REDIS_URL."
            )

        self._key_prefix = key_prefix
        # We use a SINGLE client for the entire process.
        self._client = get_redis_client()
        self._serializer = PickleSerializer()

    def _handle_redis_error(self, exc: RedisError) -> None:
        raise NovaCacheError(f"Redis backend operation failed: {exc}") from exc

    def get(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            return self._serializer.loads(raw)
        except RedisError as e:
            self._handle_redis_error(e)

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            payload = self._serializer.dumps(value)
            self._client.set(key, payload, ex=ttl)
        except RedisError as e:
            self._handle_redis_error(e)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except RedisError as e:
            self._handle_redis_error(e)

    def clear(self) -> None:
        try:
            if not self._key_prefix:
                raise NovaCacheError(
                    "Cannot clear Redis safely without a key_prefix. "
                    "Refusing to run FLUSHDB to protect shared database data."
                )

            pattern = f"{self._key_prefix}:*"
            cursor = 0
            while True:
                cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    self._client.delete(*keys)
                if cursor == 0:
                    break
        except RedisError as e:
            self._handle_redis_error(e)

    def stats(self) -> dict[str, Any]:
        try:
            info = self._client.info(section="memory")
            return {
                "backend": "redis",
                "used_memory": info.get("used_memory_human"),
                "keys": self._client.dbsize(),
            }
        except RedisError as e:
            self._handle_redis_error(e)
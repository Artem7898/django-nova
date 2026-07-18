"""
Redis cache backend.

Requires:
    pip install "django-nova[cache]"
"""

from __future__ import annotations

from typing import Any

try:
    import redis
    from redis.exceptions import RedisError
except ImportError:
    redis = None
    # A type stub so that mypy doesn't swear if the package isn't installed
    RedisError = Exception

from nova.cache.pool import get_redis_pool
from nova.cache.serializers import PickleSerializer

from nova.cache.backends.protocol import CacheBackend
from nova.core.exceptions import NovaCacheError


class RedisCacheBackend(CacheBackend):
    """
    Production-ready, safe Redis cache backend.
    Translates low-level redis exceptions to NovaCacheError.
    """

    def __init__(
        self,
        url: str,
        key_prefix: str = "nova",
    ) -> None:
        if redis is None:
            raise NovaCacheError(
                "Redis package is not installed. Install it via: pip install 'django-nova[cache]'"
            )

        self._key_prefix = key_prefix
        pool = get_redis_pool(url)
        self._client = redis.Redis(connection_pool=pool)
        self._serializer = PickleSerializer()

    def _handle_redis_error(self, exc: RedisError) -> None:
        """Error isolation: we convert Redis exceptions to Nova errors."""
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
        """
       Secure cache cleanup.
        Uses SCAN to delete only keys with our prefix.
        DOES NOT USE flushdb() to avoid killing other people's data in Redis!
        """
        try:
            if not self._key_prefix:
                raise NovaCacheError(
                    "Cannot clear Redis safely without a key_prefix. "
                    "Refusing to run FLUSHDB to protect shared database data."
                )

            pattern = f"{self._key_prefix}:*"
            cursor = 0
            while True:
                # SCAN does not block Redis (unlike KEYS*)
                cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    self._client.delete(*keys)
                # Cursor returns 0 when the crawl is completed
                if cursor == 0:
                    break
        except RedisError as e:
            self._handle_redis_error(e)

    def stats(self) -> dict[str, Any]:
        try:
            # We only request the memory section so as not to load Redis.
            info = self._client.info(section="memory")

            return {
                "backend": "redis",
                "used_memory": info.get("used_memory_human"),
                # dbsize returns the number of keys in the current database
                "keys": self._client.dbsize(),
            }
        except RedisError as e:
            self._handle_redis_error(e)
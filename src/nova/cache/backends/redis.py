"""Redis cache backend."""

from __future__ import annotations

import logging
from typing import Any, Final

from nova.cache.backends.protocol import CacheBackend
from nova.core.exceptions import NovaCacheError

logger = logging.getLogger(__name__)

_redis_available: bool = True
try:
    from redis.exceptions import RedisError  # type: ignore[reportMissingImports]
except ImportError:
    class RedisError(Exception):  # type: ignore[no-redef]
        pass
    _redis_available = False

REDIS_AVAILABLE: Final[bool] = _redis_available


class RedisCacheBackend(CacheBackend):
    """Production-ready Redis cache backend."""

    def __init__(
        self,
        url: str | None = None,
        key_prefix: str = "nova",
    ) -> None:
        from nova.redis.client import get_redis_client

        if url is not None:
            logger.warning("Passing 'url' to RedisCacheBackend is deprecated.")

        self._key_prefix: str = key_prefix
        self._client: Any = get_redis_client()

        try:
            from nova.cache.serializers import (
                PickleSerializer,  # type: ignore[reportMissingImports]
            )
            self._serializer: Any = PickleSerializer()
        except ImportError:
            self._serializer = None

    def _handle_redis_error(self, exc: Exception) -> None:
        raise NovaCacheError(f"Redis backend operation failed: {exc}") from exc

    def get(self, key: str) -> Any | None:
        try:
            raw: Any = self._client.get(key)
            if raw is None or self._serializer is None:
                return None
            return self._serializer.loads(raw)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            if self._serializer is None:
                return
            payload: Any = self._serializer.dumps(value)
            self._client.set(key, payload, ex=ttl)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def clear(self) -> None:
        try:
            if not self._key_prefix:
                raise NovaCacheError("Cannot clear Redis safely without a key_prefix.")

            pattern: str = f"{self._key_prefix}:*"
            cursor: Any = 0
            while True:
                results: Any = self._client.scan(cursor=cursor, match=pattern, count=100)
                if not results or len(results) < 2:
                    break
                cursor, keys = results[0], results[1]
                if keys:
                    self._client.delete(*keys)
                if int(cursor) == 0:
                    break
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def stats(self) -> dict[str, Any]:
        try:
            info: Any = self._client.info(section="memory")
            used_mem = info.get("used_memory_human") if hasattr(info, "get") else "Unknown"
            dbsize_val = self._client.dbsize() if hasattr(self._client, "dbsize") else 0
            return {"backend": "redis", "used_memory": used_mem, "keys": dbsize_val}
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            return {"backend": "redis", "status": "error"}
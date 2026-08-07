"""
Redis cache backend.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final

from nova.core.exceptions import NovaCacheError

from .protocol import TTL, CacheBackend
from .serializers import CacheSerializer, PickleSerializer

logger = logging.getLogger(__name__)

_redis_available: bool = True

try:
    from redis.exceptions import RedisError  # type: ignore
except ImportError:
    class RedisError(Exception):  # type: ignore[no-redef]
        pass

    _redis_available = False

REDIS_AVAILABLE: Final[bool] = _redis_available


class RedisCacheBackend(CacheBackend):
    """Production-ready Redis cache backend."""

    _serializer: CacheSerializer

    def __init__(
        self,
        url: str | None = None,
        key_prefix: str = "nova",
    ) -> None:
        from nova.redis.client import get_redis_client

        if url is not None:
            logger.warning(
                "Passing 'url' to RedisCacheBackend is deprecated."
            )

        self._key_prefix: str = key_prefix
        self._client: Any = get_redis_client()
        self._serializer = PickleSerializer()

    def _handle_redis_error(self, exc: Exception) -> None:
        raise NovaCacheError(f"Redis backend operation failed: {exc}") from exc

    def _serialize(self, value: Any) -> bytes:
        return self._serializer.dumps(value)

    def _deserialize(self, raw: Any) -> Any:
        if raw is None:
            return None

        return self._serializer.loads(raw)

    def _get_ttl_seconds(self, ttl: TTL) -> int | None:
        if ttl is None:
            return None

        if isinstance(ttl, timedelta):
            return int(ttl.total_seconds())

        return int(ttl)

    def get(self, key: str, default: Any | None = None) -> Any | None:
        try:
            raw = self._client.get(key)

            if raw is None:
                return default

            return self._deserialize(raw)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        try:
            payload = self._serialize(value)
            ex = self._get_ttl_seconds(ttl)
            self._client.set(key, payload, ex=ex)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def delete(self, key: str) -> bool:
        try:
            return bool(self._client.delete(key))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def clear(self) -> None:
        try:
            if not self._key_prefix:
                raise NovaCacheError(
                    "Cannot clear Redis safely without a key_prefix."
                )

            pattern = f"{self._key_prefix}:*"
            cursor = 0

            while True:
                results = self._client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100,
                )

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

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        try:
            if not keys:
                return {}

            raw_values = self._client.mget(keys)

            return {
                k: self._deserialize(v) if v is not None else None
                for k, v in zip(keys, raw_values, strict=True)
            }
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        try:
            if not values:
                return

            ex = self._get_ttl_seconds(ttl)

            if ex is not None:
                with self._client.pipeline(transaction=False) as pipe:
                    for k, v in values.items():
                        pipe.set(k, self._serialize(v), ex=ex)
                    pipe.execute()
            else:
                self._client.mset(
                    {k: self._serialize(v) for k, v in values.items()}
                )
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def delete_many(self, keys: list[str]) -> int:
        try:
            if not keys:
                return 0

            return int(self._client.delete(*keys))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    @property
    def backend_name(self) -> str:
        return "redis"

    @property
    def supports_ttl(self) -> bool:
        return True

    @property
    def supports_atomic_increment(self) -> bool:
        return True

    @property
    def supports_pattern_delete(self) -> bool:
        return True

    def size(self) -> int:
        try:
            return int(self._client.dbsize())
        except Exception:
            return -1

    def stats(self) -> dict[str, Any]:
        try:
            info = self._client.info(section="memory")

            used_mem = (
                info.get("used_memory_human", "Unknown")
                if hasattr(info, "get")
                else "Unknown"
            )

            return {
                "backend": self.backend_name,
                "used_memory": used_mem,
                "keys": self.size(),
            }
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)

            return {
                "backend": self.backend_name,
                "status": "error",
            }
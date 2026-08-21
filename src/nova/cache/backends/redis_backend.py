"""
Async Redis cache backend.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final

from ...core.exceptions import NovaCacheError
from .protocol import TTL, AsyncCacheBackend
from .serializers import CacheSerializer, PickleSerializer

logger = logging.getLogger(__name__)

_redis_available: bool = True
RedisError: type[Exception]

try:
    from redis.exceptions import RedisError as _RedisError  # pyright: ignore[reportMissingImports]
except ImportError:

    class _FallbackRedisError(Exception):
        pass

    RedisError = _FallbackRedisError
    _redis_available = False
else:
    RedisError = _RedisError

REDIS_AVAILABLE: Final[bool] = _redis_available


class AsyncRedisCacheBackend(AsyncCacheBackend):
    """
    Production-ready async Redis cache backend.

    Supports dependency injection for contract testing:

        AsyncRedisCacheBackend(client=fake_async_redis)
    """

    _serializer: CacheSerializer

    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        key_prefix: str = "nova",
    ) -> None:
        if url is not None:
            logger.warning(
                "Passing 'url' to AsyncRedisCacheBackend is deprecated. "
                "Use dependency injection instead."
            )

        if client is None:
            try:
                from ...redis.client import get_async_redis_client

                client = get_async_redis_client()
            except ImportError as exc:
                raise ImportError(
                    "redis is required for AsyncRedisCacheBackend. "
                    'Run: pip install "django-nova[cache]"'
                ) from exc

        self._client: Any = client
        self._key_prefix = key_prefix
        self._serializer = PickleSerializer()

    #
    # Internal helpers
    #

    def _make_key(self, key: str) -> str:
        if not self._key_prefix:
            return key

        return f"{self._key_prefix}:{key}"

    def _ttl_ms(self, ttl: TTL) -> int | None:
        if ttl is None:
            return None

        if isinstance(ttl, timedelta):
            return int(ttl.total_seconds() * 1000)

        return int(float(ttl) * 1000)

    def _serialize(self, value: Any) -> bytes:
        return self._serializer.dumps(value)

    def _deserialize(self, raw: Any) -> Any:
        if raw is None:
            return None

        return self._serializer.loads(raw)

    def _handle_redis_error(self, exc: Exception) -> None:
        raise NovaCacheError(f"Async Redis backend operation failed: {exc}") from exc

    #
    # Core operations
    #

    async def get(self, key: str, default: Any | None = None) -> Any | None:
        try:
            raw = await self._client.get(self._make_key(key))

            if raw is None:
                return default

            return self._deserialize(raw)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    async def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        try:
            payload = self._serialize(value)
            ms = self._ttl_ms(ttl)
            redis_key = self._make_key(key)

            if ms is None:
                await self._client.set(redis_key, payload)
            else:
                await self._client.set(redis_key, payload, px=max(ms, 1))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    async def delete(self, key: str) -> bool:
        try:
            return bool(await self._client.delete(self._make_key(key)))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    async def clear(self) -> None:
        try:
            if not self._key_prefix:
                raise NovaCacheError("Cannot clear Redis safely without a key_prefix.")

            pattern = f"{self._key_prefix}:*"
            cursor = 0

            while True:
                cursor, keys = await self._client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100,
                )

                if keys:
                    await self._client.delete(*keys)

                if int(cursor) == 0:
                    break
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    #
    # Bulk operations
    #

    async def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        try:
            if not keys:
                return {}

            prefixed_keys = [self._make_key(key) for key in keys]
            raw_values = await self._client.mget(prefixed_keys)

            return {
                key: self._deserialize(raw) if raw is not None else None
                for key, raw in zip(keys, raw_values, strict=True)
            }
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    async def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        try:
            if not values:
                return

            ms = self._ttl_ms(ttl)
            pipe: Any = self._client.pipeline(transaction=False)

            for key, value in values.items():
                payload = self._serialize(value)
                redis_key = self._make_key(key)

                if ms is None:
                    pipe.set(redis_key, payload)
                else:
                    pipe.set(redis_key, payload, px=max(ms, 1))

            await pipe.execute()
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    async def delete_many(self, keys: list[str]) -> int:
        try:
            if not keys:
                return 0

            prefixed_keys = [self._make_key(key) for key in keys]

            return int(await self._client.delete(*prefixed_keys))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    #
    # Introspection
    #

    @property
    def backend_name(self) -> str:
        return "async_redis"

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
        return -1

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "currsize": self.size(),
        }

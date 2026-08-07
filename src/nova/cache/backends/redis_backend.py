"""
Async Redis cache backend.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from nova.core.exceptions import NovaCacheError

from .protocol import TTL, AsyncCacheBackend
from .serializers import CacheSerializer, PickleSerializer

logger = logging.getLogger(__name__)

_async_redis_available: bool = True

try:
    from redis.asyncio import Redis as AsyncRedis  # type: ignore
    from redis.exceptions import RedisError  # type: ignore
except ImportError:
    class RedisError(Exception):  # type: ignore[no-redef]
        ...

    class AsyncRedis:  # type: ignore[no-redef]
        @classmethod
        def from_url(cls, *args: Any, **kwargs: Any) -> Any:
            ...

        async def get(self, *args: Any, **kwargs: Any) -> Any:
            ...

        async def set(self, *args: Any, **kwargs: Any) -> Any:
            ...

        async def delete(self, *args: Any, **kwargs: Any) -> Any:
            ...

        async def scan(self, *args: Any, **kwargs: Any) -> Any:
            ...

        async def mget(self, *args: Any, **kwargs: Any) -> Any:
            ...

        def pipeline(self, *args: Any, **kwargs: Any) -> Any:
            ...

    _async_redis_available = False


class AsyncRedisCacheBackend(AsyncCacheBackend):
    """Production-ready async Redis cache backend."""

    _serializer: CacheSerializer

    def __init__(
        self,
        url: str | None = None,
        key_prefix: str = "nova",
    ) -> None:
        if not _async_redis_available:
            raise ImportError(
                "redis[hiredis] is required for AsyncRedisCacheBackend. "
                'Run: pip install "django-nova[cache]"'
            )

        try:
            from nova.redis.client import get_async_redis_client

            self._client: Any = get_async_redis_client()
        except ImportError:
            self._client = AsyncRedis.from_url(
                url or "redis://localhost:6379/0",
                decode_responses=False,
            )

        self._key_prefix = key_prefix
        self._serializer = PickleSerializer()

    def _get_ttl_seconds(self, ttl: TTL) -> int | None:
        if ttl is None:
            return None

        if isinstance(ttl, timedelta):
            return int(ttl.total_seconds())

        return int(ttl)

    def _serialize(self, value: Any) -> bytes:
        return self._serializer.dumps(value)

    def _deserialize(self, raw: Any) -> Any:
        if raw is None:
            return None

        return self._serializer.loads(raw)

    async def get(self, key: str, default: Any | None = None) -> Any | None:
        try:
            raw = await self._client.get(key)

            if raw is None:
                return default

            return self._deserialize(raw)
        except RedisError as e:
            raise NovaCacheError(f"Async Redis get failed: {e}") from e

    async def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        try:
            payload = self._serialize(value)
            ex = self._get_ttl_seconds(ttl)
            await self._client.set(key, payload, ex=ex)
        except RedisError as e:
            raise NovaCacheError(f"Async Redis set failed: {e}") from e

    async def delete(self, key: str) -> bool:
        try:
            return bool(await self._client.delete(key))
        except RedisError as e:
            raise NovaCacheError(f"Async Redis delete failed: {e}") from e

    async def clear(self) -> None:
        try:
            if not self._key_prefix:
                raise NovaCacheError(
                    "Cannot clear Redis safely without a key_prefix."
                )

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
        except RedisError as e:
            raise NovaCacheError(f"Async Redis clear failed: {e}") from e

    async def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        try:
            if not keys:
                return {}

            raw_values = await self._client.mget(keys)

            return {
                k: self._deserialize(v) if v is not None else None
                for k, v in zip(keys, raw_values, strict=True)
            }
        except RedisError as e:
            raise NovaCacheError(f"Async Redis get_many failed: {e}") from e

    async def set_many(
        self,
        values: Mapping[str, Any],
        *,
        ttl: TTL = None,
    ) -> None:
        try:
            if not values:
                return

            ex = self._get_ttl_seconds(ttl)
            pipe = self._client.pipeline(transaction=False)

            for k, v in values.items():
                payload = self._serialize(v)
                pipe.set(k, payload, ex=ex)

            await pipe.execute()
        except RedisError as e:
            raise NovaCacheError(f"Async Redis set_many failed: {e}") from e

    async def delete_many(self, keys: list[str]) -> int:
        try:
            if not keys:
                return 0

            return int(await self._client.delete(*keys))
        except RedisError as e:
            raise NovaCacheError(f"Async Redis delete_many failed: {e}") from e

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
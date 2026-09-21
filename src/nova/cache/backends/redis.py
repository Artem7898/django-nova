"""
Redis cache backend.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final, cast

from ...core.exceptions import NovaCacheError
from ..generation import (
    GenerationUnavailableError,
    GenerationWriter,
    generation_key,
    new_generation,
    validate_generation,
)
from .protocol import TTL, CacheBackend
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


class RedisCacheBackend(CacheBackend):
    """
    Production-ready Redis cache backend.

    Supports dependency injection for contract testing:

        RedisCacheBackend(client=fake_redis_client)
    """

    _serializer: CacheSerializer

    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        key_prefix: str = "nova",
        generation_writer: GenerationWriter | None = None,
    ) -> None:
        if url is not None:
            logger.warning(
                "Passing 'url' to RedisCacheBackend is deprecated. "
                "Use dependency injection instead."
            )

        if client is None:
            from ...redis.client import get_redis_client

            client = get_redis_client()

        self._client: Any = client
        self._key_prefix = key_prefix
        self._serializer = PickleSerializer()
        self._generation_writer = generation_writer

    #
    # Internal helpers
    #

    @property
    def returns_detached_values(self) -> bool:
        """Native pickle reads own their result; custom implementations opt in."""
        return type(self) is RedisCacheBackend and type(self._serializer) is PickleSerializer

    @property
    def stores_detached_values(self) -> bool:
        """Native set serializes to immutable bytes before invoking the client."""
        return type(self) is RedisCacheBackend and type(self._serializer) is PickleSerializer

    def _make_key(self, key: str) -> str:
        if not self._key_prefix:
            return key

        return f"{self._key_prefix}:{key}"

    def get_generation(self, scope: str) -> str:
        """Read a shared token, using atomic create-if-absent after eviction."""
        key = self._make_key(generation_key(scope))
        try:
            writer = self._get_generation_writer()
            for _ in range(3):
                raw: object = self._client.get(key)
                if raw is not None:
                    return validate_generation(raw)
                # No TTL on metadata. Eviction is still safe because each new
                # token is unique. Never overwrite a concurrent invalidation.
                writer.write(key, new_generation().encode("ascii"), only_if_absent=True)
            raise GenerationUnavailableError("Shared cache generation disappeared repeatedly")
        except Exception as exc:
            if isinstance(exc, GenerationUnavailableError):
                raise
            raise GenerationUnavailableError("Cannot read Redis cache generation") from exc

    def get_generations(self, scopes: tuple[str, ...]) -> dict[str, str]:
        """Read warm metadata in one MGET; safely initialize only missing keys."""
        scopes = tuple(dict.fromkeys(scopes))
        if not scopes:
            return {}
        try:
            # Preserve the single-attempt writer requirement even on warm reads.
            self._get_generation_writer()
            read_many: Any = getattr(self._client, "mget", None)
            if not callable(read_many):
                return {scope: self.get_generation(scope) for scope in scopes}
            keys = [self._make_key(generation_key(scope)) for scope in scopes]
            raw: object = read_many(keys)
            if not isinstance(raw, (list, tuple)):
                raise GenerationUnavailableError("Invalid Redis generation batch response")
            values = cast("list[object] | tuple[object, ...]", raw)
            if len(values) != len(scopes):
                raise GenerationUnavailableError("Incomplete Redis generation batch response")
            # Validate all present tokens before attempting any initialization.
            tokens = {
                scope: validate_generation(value)
                for scope, value in zip(scopes, values, strict=True)
                if value is not None
            }
            for scope, value in zip(scopes, values, strict=True):
                if value is None:
                    # MGET also returns None for non-string keys. GET detects
                    # WRONGTYPE without overwriting that corrupt metadata.
                    tokens[scope] = self.get_generation(scope)
            return tokens
        except Exception as exc:
            if isinstance(exc, GenerationUnavailableError):
                raise
            raise GenerationUnavailableError("Cannot read Redis cache generations") from exc

    def rotate_generation(self, scope: str) -> str:
        """Install a fresh token even if this process knows no query keys."""
        token = new_generation()
        try:
            if (
                self._get_generation_writer().write(
                    self._make_key(generation_key(scope)),
                    token.encode("ascii"),
                    only_if_absent=False,
                )
                is not True
            ):
                raise GenerationUnavailableError("Redis did not acknowledge generation rotation")
        except Exception as exc:
            if isinstance(exc, GenerationUnavailableError):
                raise
            raise GenerationUnavailableError("Cannot rotate Redis cache generation") from exc
        return token

    def _get_generation_writer(self) -> GenerationWriter:
        if self._generation_writer is None:
            from ..generation_transport import redis_generation_writer

            self._generation_writer = redis_generation_writer(self._client)
        return self._generation_writer

    def _ttl_ms(self, ttl: TTL) -> int | None:
        if ttl is None:
            return None

        seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else float(ttl)
        if seconds <= 0:
            return 0
        return max(1, int(seconds * 1000))

    def _serialize(self, value: Any) -> bytes:
        return self._serializer.dumps(value)

    def _deserialize(self, raw: Any) -> Any:
        if raw is None:
            return None

        return self._serializer.loads(raw)

    def _handle_redis_error(self, exc: Exception) -> None:
        raise NovaCacheError(f"Redis backend operation failed: {exc}") from exc

    #
    # Core operations
    #

    def get(self, key: str, default: Any | None = None) -> Any | None:
        try:
            raw = self._client.get(self._make_key(key))

            if raw is None:
                return default

            return self._deserialize(raw)
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        try:
            ms = self._ttl_ms(ttl)
            redis_key = self._make_key(key)
            if ms is not None and ms <= 0:
                self._client.delete(redis_key)
                return
            payload = self._serialize(value)

            if ms is None:
                self._client.set(redis_key, payload)
            else:
                self._client.set(redis_key, payload, px=max(ms, 1))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def delete(self, key: str) -> bool:
        try:
            return bool(self._client.delete(self._make_key(key)))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def clear(self) -> None:
        try:
            if not self._key_prefix:
                raise NovaCacheError("Cannot clear Redis safely without a key_prefix.")

            # SCAN MATCH interprets Redis glob syntax. The namespace is
            # literal; only the final wildcard should match arbitrary keys.
            escaped_prefix = "".join(
                "\\" + char if char in "\\*?[]" else char for char in self._key_prefix
            )
            pattern = f"{escaped_prefix}:*"
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

    #
    # Bulk operations
    #

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        try:
            if not keys:
                return {}

            prefixed_keys = [self._make_key(key) for key in keys]
            raw_values = self._client.mget(prefixed_keys)

            return {
                key: self._deserialize(raw) if raw is not None else None
                for key, raw in zip(keys, raw_values, strict=True)
            }
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        try:
            if not values:
                return

            ms = self._ttl_ms(ttl)
            if ms is not None and ms <= 0:
                self._client.delete(*(self._make_key(key) for key in values))
                return

            with self._client.pipeline(transaction=False) as pipe:
                for key, value in values.items():
                    payload = self._serialize(value)
                    redis_key = self._make_key(key)

                    if ms is None:
                        pipe.set(redis_key, payload)
                    else:
                        pipe.set(redis_key, payload, px=max(ms, 1))

                pipe.execute()
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    def delete_many(self, keys: list[str]) -> int:
        try:
            if not keys:
                return 0

            prefixed_keys = [self._make_key(key) for key in keys]

            return int(self._client.delete(*prefixed_keys))
        except Exception as e:
            if REDIS_AVAILABLE and isinstance(e, RedisError):
                self._handle_redis_error(e)
            raise e

    #
    # Introspection
    #

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
                info.get("used_memory_human", "Unknown") if hasattr(info, "get") else "Unknown"
            )

            return {
                "backend": self.backend_name,
                "used_memory": used_mem,
                "keys": self.size(),
            }
        except Exception:
            return {
                "backend": self.backend_name,
                "status": "error",
            }

"""
Memcached cache backend.

Requires pymemcache for production usage.
Supports dependency injection for contract testing.
"""

from __future__ import annotations

import importlib
import math
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, Protocol, cast

from .protocol import TTL, CacheBackend
from .serializers import CacheSerializer, PickleSerializer


class _MemcachedClient(Protocol):
    """
    Minimal structural contract for the memcached client.

    Django Nova only depends on the operations it actually uses.
    """

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, expire: int = 0) -> Any: ...

    def delete(self, key: str) -> Any: ...

    def flush_all(self) -> Any: ...


_MemcachedClientFactory = Callable[..., _MemcachedClient]

_memcached_available: bool = False
PyMemcacheClient: _MemcachedClientFactory | None = None

try:
    _pymemcache_base = importlib.import_module("pymemcache.client.base")
except ImportError:
    _pymemcache_base = None

if _pymemcache_base is not None:
    _client_cls: object = getattr(_pymemcache_base, "Client", None)

    if _client_cls is not None:
        PyMemcacheClient = cast(_MemcachedClientFactory, _client_cls)
        _memcached_available = True


_MISSING: object = object()


class MemcachedCacheBackend(CacheBackend):
    """
    Memcached cache backend.

    Supports dependency injection:

        MemcachedCacheBackend(client=fake_client)

    Sub-second TTL is enforced at application level because memcached
    expire granularity is one second.
    """

    _serializer: CacheSerializer

    def __init__(
        self,
        server: str = "127.0.0.1:11211",
        *,
        client: _MemcachedClient | None = None,
    ) -> None:
        if client is None:
            if not _memcached_available or PyMemcacheClient is None:
                raise ImportError("pymemcache is required for MemcachedCacheBackend")

            client = PyMemcacheClient((server,))

        self._client: _MemcachedClient = client
        self._serializer = PickleSerializer()

    #
    # Internal helpers
    #

    def _ttl_seconds(self, ttl: TTL) -> float | None:
        if ttl is None:
            return None

        if isinstance(ttl, timedelta):
            return ttl.total_seconds()

        return float(ttl)

    def _memcached_expire(self, ttl: TTL) -> int:
        seconds = self._ttl_seconds(ttl)

        if seconds is None:
            return 0

        if seconds <= 0:
            return 0

        return max(1, math.ceil(seconds))

    def _pack(self, value: Any, ttl: TTL) -> bytes:
        seconds = self._ttl_seconds(ttl)

        expires_at: float | None = None if seconds is None else time.monotonic() + seconds

        return self._serializer.dumps((expires_at, value))

    def _unpack(self, raw: bytes | None) -> Any:
        if raw is None:
            return _MISSING

        loaded: object = self._serializer.loads(raw)

        if not isinstance(loaded, tuple):
            return _MISSING

        envelope = cast("tuple[object, ...]", loaded)

        if len(envelope) != 2:
            return _MISSING

        expires_at: object = envelope[0]
        value: object = envelope[1]

        if expires_at is not None:
            if not isinstance(expires_at, int | float):
                return _MISSING

            if time.monotonic() >= float(expires_at):
                return _MISSING

        return value

    #
    # Core operations
    #

    def get(self, key: str, default: Any | None = None) -> Any | None:
        raw = self._client.get(key)
        value = self._unpack(raw)

        if value is _MISSING:
            return default

        return value

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        payload = self._pack(value, ttl)
        expire = self._memcached_expire(ttl)

        self._client.set(key, payload, expire=expire)

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(key))

    def clear(self) -> None:
        self._client.flush_all()

    #
    # Bulk operations
    #

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        return {key: self.get(key) for key in keys}

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        for key, value in values.items():
            self.set(key, value, ttl=ttl)

    def delete_many(self, keys: list[str]) -> int:
        count = 0

        for key in keys:
            if self.delete(key):
                count += 1

        return count

    #
    # Introspection
    #

    @property
    def backend_name(self) -> str:
        return "memcached"

    @property
    def supports_ttl(self) -> bool:
        return True

    @property
    def supports_atomic_increment(self) -> bool:
        return True

    @property
    def supports_pattern_delete(self) -> bool:
        return False

    def size(self) -> int:
        return -1

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "currsize": self.size(),
            "maxsize": None,
            "ttl": None,
        }

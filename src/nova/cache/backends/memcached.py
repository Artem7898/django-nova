"""
Memcached cache backend.

Requires pymemcache.
"""

from __future__ import annotations

import importlib
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

    def get(self, key: str) -> bytes | None:
        ...

    def set(self, key: str, value: bytes, expire: int = 0) -> bool:
        ...

    def delete(self, key: str) -> bool:
        ...

    def flush_all(self) -> bool:
        ...


_MemcachedClientFactory = Callable[..., _MemcachedClient]

_memcached_available: bool = False
PyMemcacheClient: _MemcachedClientFactory | None = None

try:
    _pymemcache_base = importlib.import_module("pymemcache.client.base")
except ImportError:
    _pymemcache_base = None

if _pymemcache_base is not None:
    _client_cls: Any = getattr(_pymemcache_base, "Client", None)

    if _client_cls is not None:
        PyMemcacheClient = cast(_MemcachedClientFactory, _client_cls)
        _memcached_available = True


class MemcachedCacheBackend(CacheBackend):
    """Memcached cache backend."""

    _serializer: CacheSerializer

    def __init__(self, server: str = "127.0.0.1:11211") -> None:
        if not _memcached_available or PyMemcacheClient is None:
            raise ImportError(
                "pymemcache is required for MemcachedCacheBackend"
            )

        self._client: _MemcachedClient = PyMemcacheClient((server,))
        self._serializer = PickleSerializer()

    def _get_expire(self, ttl: TTL) -> int:
        if ttl is None:
            return 0

        if isinstance(ttl, timedelta):
            return int(ttl.total_seconds())

        return int(ttl)

    def _serialize(self, value: Any) -> bytes:
        return self._serializer.dumps(value)

    def _deserialize(self, raw: bytes | None) -> Any:
        if raw is None:
            return None

        return self._serializer.loads(raw)

    def get(self, key: str, default: Any | None = None) -> Any | None:
        raw = self._client.get(key)

        if raw is None:
            return default

        return self._deserialize(raw)

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        val = self._serialize(value)
        self._client.set(key, val, expire=self._get_expire(ttl))

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(key))

    def clear(self) -> None:
        self._client.flush_all()

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        return {k: self.get(k) for k in keys}

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        expire = self._get_expire(ttl)

        for k, v in values.items():
            val = self._serialize(v)
            self._client.set(k, val, expire=expire)

    def delete_many(self, keys: list[str]) -> int:
        count = 0

        for k in keys:
            if self.delete(k):
                count += 1

        return count

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
            "maxsize": -1,
            "ttl": None,
        }
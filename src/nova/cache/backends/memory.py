"""
In-memory cache backend.

Suitable for:

- unit tests
- local development
- single process deployments

Not suitable for:

- multiple workers
- Kubernetes
- horizontal scaling
"""

from __future__ import annotations

from typing import Any

from cachetools import TTLCache

from nova.cache.backends.protocol import CacheBackend


class MemoryCacheBackend(CacheBackend):
    """
    Default cache backend.
    """

    def __init__(
        self,
        *,
        maxsize: int = 1000,
        ttl: int = 60,
    ) -> None:
        self._cache: TTLCache[str, Any] = TTLCache(
            maxsize=maxsize,
            ttl=ttl,
        )

        self._ttl = ttl
        self._maxsize = maxsize

    def get(
        self,
        key: str,
    ) -> Any | None:
        return self._cache.get(key)

    def set(
        self,
        key: str,
        value: Any,
        ttl: int,
    ) -> None:
        # TTLCache only supports global TTL.
        # Per-key TTL support will be provided by Redis backend.
        self._cache[key] = value

    def delete(
        self,
        key: str,
    ) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "memory",
            "currsize": self._cache.currsize,
            "maxsize": self._maxsize,
            "ttl": self._ttl,
        }
"""
Async in-memory cache backend.

Suitable for:
- async unit tests
- local async development
- single-event-loop deployments
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from nova.cache.backends.protocol import TTL, AsyncCacheBackend


class AsyncIOCacheBackend(AsyncCacheBackend):
    """Async-safe in-memory cache backend using asyncio.Lock."""

    def __init__(self, *, maxsize: int = 1000) -> None:
        self._store: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    async def get(self, key: str, default: Any | None = None) -> Any | None:
        # Dict reads in CPython are thread-safe due to GIL,
        # but using a lock is strictly correct for async paradigms.
        async with self._lock:
            return self._store.get(key, default)

    async def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        # Note: Pure dict doesn't support TTL.
        # For TTL support in async, use AsyncRedisBackend.
        async with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                # Simple eviction strategy (FIFO-ish)
                oldest_key = next(iter(self._store))
                del self._store[oldest_key]
            self._store[key] = value

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        async with self._lock:
            return {k: self._store.get(k) for k in keys}

    async def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        async with self._lock:
            self._store.update(values)

    async def delete_many(self, keys: list[str]) -> int:
        count = 0
        async with self._lock:
            for k in keys:
                if k in self._store:
                    del self._store[k]
                    count += 1
        return count

    @property
    def backend_name(self) -> str:
        return "asyncio"

    @property
    def supports_ttl(self) -> bool:
        return False

    @property
    def supports_atomic_increment(self) -> bool:
        return False

    @property
    def supports_pattern_delete(self) -> bool:
        return False

    def size(self) -> int:
        return len(self._store)

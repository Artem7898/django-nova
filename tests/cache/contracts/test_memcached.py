"""
Contract tests for Memcached cache backend.

These tests use an in-memory fake memcached client,
so no real Memcached server is required.
"""

from __future__ import annotations

from tests.cache.contracts import CacheBackendContract

from nova.cache.backends.memcached import MemcachedCacheBackend


class FakeMemcachedClient:
    """
    In-memory fake implementing the minimal memcached client contract.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def set(self, key: str, value: bytes, expire: int = 0) -> bool:
        self.store[key] = value
        return True

    def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None

    def flush_all(self) -> bool:
        self.store.clear()
        return True


class TestMemcachedCacheBackendContract(CacheBackendContract):
    def create_backend(self) -> MemcachedCacheBackend:
        return MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )
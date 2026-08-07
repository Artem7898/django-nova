"""
Cache architecture contracts.

These tests verify that cache backends honor the Nova CacheBackend contract.
They test architectural promises, not implementation details.
"""

from __future__ import annotations

import asyncio

import pytest
from tests.cache.contracts import CacheBackendContract

from nova.cache.backends.asyncio_backend import AsyncIOCacheBackend
from nova.cache.backends.django_cache import DjangoCacheBackend
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.null import NullCacheBackend
from nova.cache.backends.protocol import AsyncCacheBackend


class TestMemoryBackendContract(CacheBackendContract):
    def create_backend(self) -> MemoryCacheBackend:
        return MemoryCacheBackend(maxsize=1000, ttl=60)


class TestNullBackendContract(CacheBackendContract):
    def create_backend(self) -> NullCacheBackend:
        backend = NullCacheBackend()
        backend.clear()
        return backend


class TestDjangoCacheBackendContract(CacheBackendContract):
    def create_backend(self) -> DjangoCacheBackend:
        backend = DjangoCacheBackend(alias="default")
        backend.clear()
        return backend


class AsyncCacheBackendContract:
    """
    Behavior contract implemented by every async cache backend.
    """

    def create_backend(self) -> AsyncCacheBackend:
        raise NotImplementedError("Subclasses must provide a backend instance")

    async def test_set_then_get(self) -> None:
        backend = self.create_backend()

        await backend.set("key1", "value1")

        assert await backend.get("key1") == "value1"

    async def test_missing_returns_default(self) -> None:
        backend = self.create_backend()

        assert await backend.get("non_existent") is None
        assert await backend.get("non_existent", default="default_val") == "default_val"

    async def test_overwrite(self) -> None:
        backend = self.create_backend()

        await backend.set("key", "val1")
        await backend.set("key", "val2")

        assert await backend.get("key") == "val2"

    async def test_delete_existing_key(self) -> None:
        backend = self.create_backend()

        await backend.set("key", "val")

        result = await backend.delete("key")

        assert result is True
        assert await backend.get("key") is None

    async def test_delete_missing_key(self) -> None:
        backend = self.create_backend()

        result = await backend.delete("non_existent")

        assert result is False

    async def test_clear(self) -> None:
        backend = self.create_backend()

        await backend.set("k1", "v1")
        await backend.set("k2", "v2")

        await backend.clear()

        assert await backend.get("k1") is None
        assert await backend.get("k2") is None

    async def test_ttl_expiration(self) -> None:
        backend = self.create_backend()

        if not backend.supports_ttl:
            pytest.skip("Backend does not support TTL")

        await backend.set("ttl_key", "val", ttl=0.1)
        assert await backend.get("ttl_key") == "val"

        await asyncio.sleep(0.15)

        assert await backend.get("ttl_key") is None

    async def test_store_none(self) -> None:
        backend = self.create_backend()

        await backend.set("none_key", None)

        assert await backend.get("none_key", default="MISS") is None
        assert await backend.get("real_miss", default="MISS") == "MISS"

    async def test_roundtrip_dict(self) -> None:
        backend = self.create_backend()

        data = {"a": 1, "b": [1, 2, 3]}

        await backend.set("dict_key", data)

        assert await backend.get("dict_key") == data

    async def test_roundtrip_list(self) -> None:
        backend = self.create_backend()

        data = [1, "two", 3.0]

        await backend.set("list_key", data)

        assert await backend.get("list_key") == data

    async def test_roundtrip_nested(self) -> None:
        backend = self.create_backend()

        data = {"user": {"id": 1, "tags": ["admin", "staff"]}}

        await backend.set("nested_key", data)

        assert await backend.get("nested_key") == data

    async def test_get_many(self) -> None:
        backend = self.create_backend()

        await backend.set("k1", "v1")
        await backend.set("k2", "v2")

        result = await backend.get_many(["k1", "k2", "k_missing"])

        assert result["k1"] == "v1"
        assert result["k2"] == "v2"
        assert result["k_missing"] is None

    async def test_set_many(self) -> None:
        backend = self.create_backend()

        await backend.set_many({"mk1": "mv1", "mk2": "mv2"})

        assert await backend.get("mk1") == "mv1"
        assert await backend.get("mk2") == "mv2"

    async def test_delete_many(self) -> None:
        backend = self.create_backend()

        await backend.set("dk1", "v1")
        await backend.set("dk2", "v2")

        deleted_count = await backend.delete_many(["dk1", "dk2", "dk_missing"])

        assert deleted_count >= 2
        assert await backend.get("dk1") is None

    async def test_backend_name(self) -> None:
        backend = self.create_backend()

        name = backend.backend_name

        assert isinstance(name, str)
        assert len(name) > 0

    async def test_size(self) -> None:
        backend = self.create_backend()

        size = backend.size()

        assert isinstance(size, int)
        assert size >= -1

    async def test_support_flags(self) -> None:
        backend = self.create_backend()

        assert isinstance(backend.supports_ttl, bool)
        assert isinstance(backend.supports_atomic_increment, bool)
        assert isinstance(backend.supports_pattern_delete, bool)


class TestAsyncIOBackendContract(AsyncCacheBackendContract):
    def create_backend(self) -> AsyncIOCacheBackend:
        return AsyncIOCacheBackend(maxsize=1000)
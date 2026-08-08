"""
Base behavior contract for all asynchronous cache backends.
"""

from __future__ import annotations

import asyncio

import pytest

from nova.cache.backends.protocol import AsyncCacheBackend


class AsyncCacheBackendContract:
    """
    Behavior contract implemented by every async cache backend.

    Subclasses must implement `create_backend()` to provide a fresh instance.

    All test_* methods contain the ACTUAL assertions.
    Subclasses should NOT override them.
    """

    def create_backend(self) -> AsyncCacheBackend:
        """
        Return a fresh, isolated async backend instance for the test.
        """
        raise NotImplementedError("Subclasses must provide a backend instance")

    #
    # Core CRUD behavior
    #

    async def test_set_then_get(self) -> None:
        b = self.create_backend()

        await b.set("key1", "value1")

        assert await b.get("key1") == "value1"

    async def test_missing_returns_default(self) -> None:
        b = self.create_backend()

        assert await b.get("non_existent") is None
        assert await b.get("non_existent", default="default_val") == "default_val"

    async def test_overwrite(self) -> None:
        b = self.create_backend()

        await b.set("key", "val1")
        await b.set("key", "val2")

        assert await b.get("key") == "val2"

    async def test_delete_existing_key(self) -> None:
        b = self.create_backend()

        await b.set("key", "val")

        result = await b.delete("key")

        assert result is True
        assert await b.get("key") is None

    async def test_delete_missing_key(self) -> None:
        b = self.create_backend()

        result = await b.delete("non_existent")

        assert result is False

    async def test_clear(self) -> None:
        b = self.create_backend()

        await b.set("k1", "v1")
        await b.set("k2", "v2")

        await b.clear()

        assert await b.get("k1") is None
        assert await b.get("k2") is None

    #
    # TTL behavior
    #

    async def test_ttl_expiration(self) -> None:
        b = self.create_backend()

        if not b.supports_ttl:
            pytest.skip("Backend does not support per-key or global TTL")

        await b.set("ttl_key", "val", ttl=0.1)
        assert await b.get("ttl_key") == "val"

        await asyncio.sleep(0.15)

        assert await b.get("ttl_key") is None

    #
    # Edge cases and serializers
    #

    async def test_store_none(self) -> None:
        """Ensure backend doesn't confuse None value with cache miss."""
        b = self.create_backend()

        await b.set("none_key", None)

        assert await b.get("none_key", default="MISS") is None
        assert await b.get("real_miss", default="MISS") == "MISS"

    async def test_roundtrip_dict(self) -> None:
        b = self.create_backend()

        data = {"a": 1, "b": [1, 2, 3]}

        await b.set("dict_key", data)

        assert await b.get("dict_key") == data

    async def test_roundtrip_list(self) -> None:
        b = self.create_backend()

        data = [1, "two", 3.0]

        await b.set("list_key", data)

        assert await b.get("list_key") == data

    async def test_roundtrip_nested(self) -> None:
        b = self.create_backend()

        data = {"user": {"id": 1, "tags": ["admin", "staff"]}}

        await b.set("nested_key", data)

        assert await b.get("nested_key") == data

    #
    # Bulk operations
    #

    async def test_get_many(self) -> None:
        b = self.create_backend()

        await b.set("k1", "v1")
        await b.set("k2", "v2")

        result = await b.get_many(["k1", "k2", "k_missing"])

        assert result["k1"] == "v1"
        assert result["k2"] == "v2"
        assert result["k_missing"] is None

    async def test_set_many(self) -> None:
        b = self.create_backend()

        await b.set_many({"mk1": "mv1", "mk2": "mv2"})

        assert await b.get("mk1") == "mv1"
        assert await b.get("mk2") == "mv2"

    async def test_delete_many(self) -> None:
        b = self.create_backend()

        await b.set("dk1", "v1")
        await b.set("dk2", "v2")

        deleted_count = await b.delete_many(["dk1", "dk2", "dk_missing"])

        assert deleted_count >= 2
        assert await b.get("dk1") is None

    #
    # Backend metadata / introspection
    #

    async def test_backend_name(self) -> None:
        b = self.create_backend()

        name = b.backend_name

        assert isinstance(name, str)
        assert len(name) > 0

    async def test_size(self) -> None:
        b = self.create_backend()

        size = b.size()

        assert isinstance(size, int)
        assert size >= -1

    async def test_support_flags(self) -> None:
        b = self.create_backend()

        assert isinstance(b.supports_ttl, bool)
        assert isinstance(b.supports_atomic_increment, bool)
        assert isinstance(b.supports_pattern_delete, bool)

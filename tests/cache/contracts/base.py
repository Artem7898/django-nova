from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from nova.cache.backends.protocol import CacheBackend


@dataclass(frozen=True)
class CacheBackendExpectation:
    target: str
    factory: Callable[[], CacheBackend]
    supports_ttl: bool


class CacheBackendContract:
    """
    Behavior contract implemented by every cache backend.

    Subclasses must implement `create_backend()` to provide a fresh instance.
    All test_* methods contain the ACTUAL assertions. Subclasses should NOT override them.
    """

    def __init__(self, expectation: CacheBackendExpectation | None = None):
        self._expectation = expectation

    def create_backend(self) -> CacheBackend:
        """
        Return a fresh, isolated backend instance for the test.
        """
        if self._expectation is not None:
            # If we use the new style via expectation, we create a backend from the factory
            return self._expectation.factory()

        raise NotImplementedError("Subclasses must provide a backend instance")

    def run_all(self) -> None:
        """
        Runs all test_* methods for the passed expectation.
        """
        for name in dir(self):
            if name.startswith("test_"):
                method = getattr(self, name)
                if callable(method):
                    method()

    #
    # Core CRUD behavior
    #

    def test_set_then_get(self) -> None:
        b = self.create_backend()
        b.set("key1", "value1")
        assert b.get("key1") == "value1"

    def test_missing_returns_default(self) -> None:
        b = self.create_backend()
        assert b.get("non_existent") is None
        assert b.get("non_existent", default="default_val") == "default_val"

    def test_overwrite(self) -> None:
        b = self.create_backend()
        b.set("key", "val1")
        b.set("key", "val2")
        assert b.get("key") == "val2"

    def test_delete_existing_key(self) -> None:
        b = self.create_backend()
        b.set("key", "val")
        result = b.delete("key")

        assert result is True  # Strict Protocol requirement
        assert b.get("key") is None

    def test_delete_missing_key(self) -> None:
        b = self.create_backend()
        result = b.delete("non_existent")

        assert result is False  # Strict Protocol requirement

    def test_clear(self) -> None:
        b = self.create_backend()
        b.set("k1", "v1")
        b.set("k2", "v2")

        b.clear()

        assert b.get("k1") is None
        assert b.get("k2") is None

    #
    # TTL behavior
    #

    def test_ttl_expiration(self) -> None:
        b = self.create_backend()

        if not b.supports_ttl:
            pytest.skip("Backend does not support per-key or global TTL")

        b.set("ttl_key", "val", ttl=0.1)  # 100ms
        assert b.get("ttl_key") == "val"

        time.sleep(0.15)  # Wait for expiration

        assert b.get("ttl_key") is None

    #
    # Edge cases and Serializers
    #

    def test_store_none(self) -> None:
        """Ensure backend doesn't confuse None value with cache miss."""
        b = self.create_backend()
        b.set("none_key", None)

        # Must explicitly return None, not the default
        assert b.get("none_key", default="MISS") is None
        assert b.get("real_miss", default="MISS") == "MISS"

    def test_roundtrip_dict(self) -> None:
        b = self.create_backend()
        data = {"a": 1, "b": [1, 2, 3]}
        b.set("dict_key", data)
        assert b.get("dict_key") == data

    def test_roundtrip_list(self) -> None:
        b = self.create_backend()
        data = [1, "two", 3.0]
        b.set("list_key", data)
        assert b.get("list_key") == data

    def test_roundtrip_nested(self) -> None:
        b = self.create_backend()
        data = {"user": {"id": 1, "tags": ["admin", "staff"]}}
        b.set("nested_key", data)
        assert b.get("nested_key") == data

    #
    # Bulk Operations
    #

    def test_get_many(self) -> None:
        b = self.create_backend()
        b.set("k1", "v1")
        b.set("k2", "v2")

        result = b.get_many(["k1", "k2", "k_missing"])
        assert result["k1"] == "v1"
        assert result["k2"] == "v2"
        assert result["k_missing"] is None

    def test_set_many(self) -> None:
        b = self.create_backend()
        b.set_many({"mk1": "mv1", "mk2": "mv2"})

        assert b.get("mk1") == "mv1"
        assert b.get("mk2") == "mv2"

    def test_delete_many(self) -> None:
        b = self.create_backend()
        b.set("dk1", "v1")
        b.set("dk2", "v2")

        deleted_count = b.delete_many(["dk1", "dk2", "dk_missing"])

        # Must return at least the number of existing keys deleted
        assert deleted_count >= 2
        assert b.get("dk1") is None

    #
    # Backend Metadata (Introspection)
    #

    def test_backend_name(self) -> None:
        b = self.create_backend()
        name = b.backend_name
        assert isinstance(name, str)
        assert len(name) > 0

    def test_size(self) -> None:
        b = self.create_backend()
        size = b.size()

        # Protocol defines -1 as "unknown size"
        assert isinstance(size, int)
        assert size >= -1

    def test_support_flags(self) -> None:
        b = self.create_backend()

        # These must be boolean, not None or 0/1
        assert isinstance(b.supports_ttl, bool)
        assert isinstance(b.supports_atomic_increment, bool)
        assert isinstance(b.supports_pattern_delete, bool)

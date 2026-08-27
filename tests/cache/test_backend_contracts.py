"""
Integration-level contract tests for synchronous cache backends.

These tests verify the observable CacheBackend contract rather than
implementation details.

Nova philosophy:

    application code
            ↓
      CacheBackend protocol
            ↓
    interchangeable backends

Every backend must expose the same core semantics, while optional
behavior such as TTL is conditioned by declared capabilities.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from nova.cache.backends.django_cache import DjangoCacheBackend
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.null import NullCacheBackend
from nova.cache.backends.protocol import CacheBackend

_MISSING = object()

TTL_SECONDS = 0.05
TTL_SLEEP = 0.08


@dataclass(frozen=True, slots=True)
class BackendExpectation:
    """Expected observable capabilities of a cache backend."""

    name: str
    factory: Callable[[], CacheBackend]
    supports_ttl: bool


BACKENDS = [
    BackendExpectation(
        name="memory",
        factory=MemoryCacheBackend,
        supports_ttl=True,
    ),
    BackendExpectation(
        name="null",
        factory=NullCacheBackend,
        supports_ttl=False,
    ),
    BackendExpectation(
        name="django",
        factory=DjangoCacheBackend,
        supports_ttl=True,
    ),
]


@pytest.fixture(
    params=BACKENDS,
    ids=lambda backend: backend.name,
)
def backend_expectation(
    request: pytest.FixtureRequest,
) -> BackendExpectation:
    """Provide each backend with its declared capabilities."""
    return request.param


@pytest.fixture
def backend(
    backend_expectation: BackendExpectation,
) -> CacheBackend:
    """Return a fresh backend instance for every test."""
    instance = backend_expectation.factory()
    instance.clear()
    return instance


class TestCacheBackendProtocol:
    """Verify runtime conformance to Nova's cache protocol."""

    def test_backend_implements_protocol(
        self,
        backend: CacheBackend,
    ) -> None:
        assert isinstance(backend, CacheBackend)

    def test_backend_name_is_non_empty_string(
        self,
        backend: CacheBackend,
    ) -> None:
        assert isinstance(backend.backend_name, str)
        assert backend.backend_name

    def test_capability_flags_are_boolean(
        self,
        backend: CacheBackend,
    ) -> None:
        assert isinstance(backend.supports_ttl, bool)
        assert isinstance(
            backend.supports_atomic_increment,
            bool,
        )
        assert isinstance(
            backend.supports_pattern_delete,
            bool,
        )

    def test_size_is_valid_protocol_value(
        self,
        backend: CacheBackend,
    ) -> None:
        size = backend.size()

        assert isinstance(size, int)
        assert size >= -1


class TestCacheBackendCoreContract:
    """Core CRUD semantics shared by every synchronous backend."""

    def test_set_then_get(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("key", "value")

        assert backend.get("key") == "value"

    def test_missing_key_returns_none(
        self,
        backend: CacheBackend,
    ) -> None:
        assert backend.get("missing") is None

    def test_missing_key_returns_explicit_default(
        self,
        backend: CacheBackend,
    ) -> None:
        assert (
            backend.get(
                "missing",
                default="fallback",
            )
            == "fallback"
        )

    def test_missing_key_preserves_sentinel_default(
        self,
        backend: CacheBackend,
    ) -> None:
        assert (
            backend.get(
                "missing",
                default=_MISSING,
            )
            is _MISSING
        )

    def test_stored_none_is_not_a_cache_miss(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("key", None)

        assert (
            backend.get(
                "key",
                default=_MISSING,
            )
            is None
        )

    def test_overwrite_is_last_write_wins(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("key", "first")
        backend.set("key", "second")

        assert backend.get("key") == "second"

    def test_delete_existing_key_returns_true(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("key", "value")

        assert backend.delete("key") is True
        assert (
            backend.get(
                "key",
                default=_MISSING,
            )
            is _MISSING
        )

    def test_delete_missing_key_returns_false(
        self,
        backend: CacheBackend,
    ) -> None:
        assert backend.delete("missing") is False

    def test_clear_removes_all_values(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("a", 1)
        backend.set("b", 2)

        backend.clear()

        assert (
            backend.get(
                "a",
                default=_MISSING,
            )
            is _MISSING
        )

        assert (
            backend.get(
                "b",
                default=_MISSING,
            )
            is _MISSING
        )


class TestCacheBackendBulkContract:
    """Bulk operations must preserve single-operation semantics."""

    def test_set_many_and_get_many(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set_many(
            {
                "a": 1,
                "b": 2,
            },
        )

        result = backend.get_many(
            [
                "a",
                "b",
                "missing",
            ],
        )

        assert result["a"] == 1
        assert result["b"] == 2
        assert result["missing"] is None

    def test_set_many_overwrites_existing_values(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("a", "old")

        backend.set_many(
            {
                "a": "new",
                "b": "value",
            },
        )

        assert backend.get("a") == "new"
        assert backend.get("b") == "value"

    def test_get_many_preserves_requested_keys(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("a", 1)

        result = backend.get_many(
            [
                "a",
                "missing",
            ],
        )

        assert set(result) == {
            "a",
            "missing",
        }

    def test_delete_many_returns_exact_deleted_count(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("a", 1)
        backend.set("b", 2)

        deleted = backend.delete_many(
            [
                "a",
                "b",
                "missing",
            ],
        )

        assert deleted == 2

        assert (
            backend.get(
                "a",
                default=_MISSING,
            )
            is _MISSING
        )

        assert (
            backend.get(
                "b",
                default=_MISSING,
            )
            is _MISSING
        )

    def test_delete_many_with_only_missing_keys_returns_zero(
        self,
        backend: CacheBackend,
    ) -> None:
        deleted = backend.delete_many(
            [
                "missing-a",
                "missing-b",
            ],
        )

        assert deleted == 0


class TestCacheBackendValueContract:
    """Backends must preserve Python values through a roundtrip."""

    @pytest.mark.parametrize(
        "value",
        [
            42,
            "text",
            [1, 2, 3],
            {"name": "Nova", "version": 1},
            {
                "nested": {
                    "items": [1, 2, 3],
                    "enabled": True,
                },
            },
        ],
    )
    def test_value_roundtrip(
        self,
        backend: CacheBackend,
        value: Any,
    ) -> None:
        backend.set("value", value)

        assert backend.get("value") == value


class TestCacheBackendTTLContract:
    """TTL semantics are conditioned on backend capabilities."""

    def test_declared_ttl_capability_matches_expectation(
        self,
        backend: CacheBackend,
        backend_expectation: BackendExpectation,
    ) -> None:
        assert backend.supports_ttl is backend_expectation.supports_ttl

    def test_numeric_ttl_behavior(
        self,
        backend: CacheBackend,
        backend_expectation: BackendExpectation,
    ) -> None:
        backend.set(
            "ttl-key",
            "value",
            ttl=TTL_SECONDS,
        )

        assert backend.get("ttl-key") == "value"

        time.sleep(TTL_SLEEP)

        if backend_expectation.supports_ttl:
            assert (
                backend.get(
                    "ttl-key",
                    default=_MISSING,
                )
                is _MISSING
            )

            assert backend.delete("ttl-key") is False
        else:
            assert backend.get("ttl-key") == "value"

    def test_timedelta_ttl_is_accepted(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set(
            "timedelta-key",
            "value",
            ttl=timedelta(seconds=1),
        )

        assert backend.get("timedelta-key") == "value"


class TestCacheBackendSizeContract:
    """Validate size semantics without assuming every backend can introspect."""

    def test_empty_backend_has_valid_size(
        self,
        backend: CacheBackend,
    ) -> None:
        size = backend.size()

        assert size in {
            0,
            -1,
        }

    def test_size_after_writes_is_consistent_when_supported(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("a", 1)
        backend.set("b", 2)

        size = backend.size()

        if size != -1:
            assert size == 2

    def test_clear_resets_size_when_supported(
        self,
        backend: CacheBackend,
    ) -> None:
        backend.set("a", 1)
        backend.set("b", 2)

        backend.clear()

        size = backend.size()

        if size != -1:
            assert size == 0


class TestMemoryBackendSpecificBehavior:
    """Implementation-specific guarantees that do not belong in the protocol."""

    def test_lru_eviction_when_maxsize_exceeded(
        self,
    ) -> None:
        backend = MemoryCacheBackend(maxsize=2)

        backend.set("a", 1)
        backend.set("b", 2)
        backend.set("c", 3)

        assert backend.get("a") is None
        assert backend.get("b") == 2
        assert backend.get("c") == 3

    def test_recently_accessed_value_is_not_evicted_first(
        self,
    ) -> None:
        backend = MemoryCacheBackend(maxsize=2)

        backend.set("a", 1)
        backend.set("b", 2)

        assert backend.get("a") == 1

        backend.set("c", 3)

        assert backend.get("a") == 1
        assert backend.get("b") is None
        assert backend.get("c") == 3

"""Tests for the Memcached cache backend."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from nova.cache.backends.memcached import (
    _MISSING,
    MemcachedCacheBackend,
    PickleSerializer,
)


class FakeMemcachedClient:
    """Minimal fake Memcached client for contract tests."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int]] = []
        self.delete_calls: list[str] = []
        self.flush_calls = 0

    def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    def set(self, key: str, value: bytes, expire: int = 0) -> bool:
        self.data[key] = value
        self.set_calls.append((key, value, expire))
        return True

    def delete(self, key: str) -> bool:
        self.delete_calls.append(key)

        if key in self.data:
            del self.data[key]
            return True

        return False

    def flush_all(self) -> bool:
        self.data.clear()
        self.flush_calls += 1
        return True


class TestMemcachedCacheBackend:
    """Test the public Memcached backend contract."""

    def test_injected_client_is_used(self) -> None:
        client = FakeMemcachedClient()

        backend = MemcachedCacheBackend(client=client)

        assert backend._client is client

    def test_injected_client_does_not_require_pymemcache(self) -> None:
        client = FakeMemcachedClient()

        with patch(
            "nova.cache.backends.memcached._memcached_available",
            False,
        ):
            backend = MemcachedCacheBackend(client=client)

        assert backend._client is client

    def test_missing_pymemcache_raises_import_error(self) -> None:
        with (
            patch(
                "nova.cache.backends.memcached._memcached_available",
                False,
            ),
            patch(
                "nova.cache.backends.memcached.PyMemcacheClient",
                None,
            ),
            pytest.raises(
                ImportError,
                match="pymemcache is required for MemcachedCacheBackend",
            ),
        ):
            MemcachedCacheBackend()

    def test_pymemcache_client_is_created_from_server(self) -> None:
        client = FakeMemcachedClient()
        factory = MagicMock(return_value=client)

        with (
            patch(
                "nova.cache.backends.memcached._memcached_available",
                True,
            ),
            patch(
                "nova.cache.backends.memcached.PyMemcacheClient",
                factory,
            ),
        ):
            backend = MemcachedCacheBackend(
                server="127.0.0.1:11212",
            )

        factory.assert_called_once_with(
            ("127.0.0.1", 11212),
            default_noreply=False,
        )
        assert backend._client is client

    def test_default_server_is_used(self) -> None:
        client = FakeMemcachedClient()
        factory = MagicMock(return_value=client)

        with (
            patch(
                "nova.cache.backends.memcached._memcached_available",
                True,
            ),
            patch(
                "nova.cache.backends.memcached.PyMemcacheClient",
                factory,
            ),
        ):
            MemcachedCacheBackend()

        factory.assert_called_once_with(
            ("127.0.0.1", 11211),
            default_noreply=False,
        )

    def test_backend_name(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.backend_name == "memcached"

    def test_supports_ttl(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.supports_ttl is True

    def test_supports_atomic_increment(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.supports_atomic_increment is True

    def test_does_not_support_pattern_delete(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.supports_pattern_delete is False

    def test_size_returns_unknown_size(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.size() == -1

    def test_stats_returns_backend_metadata(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.stats() == {
            "backend": "memcached",
            "currsize": -1,
            "maxsize": None,
            "ttl": None,
        }

    def test_ttl_seconds_returns_none_for_none(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._ttl_seconds(None) is None

    def test_ttl_seconds_accepts_timedelta(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._ttl_seconds(timedelta(seconds=2.5)) == 2.5

    def test_ttl_seconds_accepts_numeric_ttl(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._ttl_seconds(5) == 5.0
        assert backend._ttl_seconds(2.5) == 2.5

    def test_memcached_expire_is_zero_for_none(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._memcached_expire(None) == 0

    def test_memcached_expire_is_zero_for_non_positive_ttl(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._memcached_expire(0) == 0
        assert backend._memcached_expire(-1) == 0

    def test_memcached_expire_rounds_subsecond_ttl_up(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._memcached_expire(0.1) == 1
        assert backend._memcached_expire(1.1) == 2

    def test_memcached_expire_rounds_timedelta_up(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert (
            backend._memcached_expire(
                timedelta(milliseconds=100),
            )
            == 1
        )

        assert (
            backend._memcached_expire(
                timedelta(seconds=1.1),
            )
            == 2
        )

    def test_pack_without_ttl_contains_no_expiration(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        payload = backend._pack("value", None)

        loaded = PickleSerializer().loads(payload)

        assert loaded == ("nova:memcached:v2", None, "value")

    def test_pack_with_ttl_contains_versioned_unix_expiration(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        with patch(
            "nova.cache.backends.memcached.time.time",
            return_value=100.0,
        ):
            payload = backend._pack(
                "value",
                5,
            )

        loaded = PickleSerializer().loads(payload)

        assert loaded == ("nova:memcached:v2", 105.0, "value")

    def test_unpack_none_returns_missing(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend._unpack(None) is backend._unpack(None)

    def test_unpack_valid_value_without_ttl(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        payload = backend._pack(
            {"name": "nova"},
            None,
        )

        assert backend._unpack(payload) == {"name": "nova"}

    def test_unpack_valid_value_with_active_ttl(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        with patch(
            "nova.cache.backends.memcached.time.time",
            side_effect=[100.0, 102.0],
        ):
            payload = backend._pack(
                "value",
                5,
            )
            assert backend._unpack(payload) == "value"

    def test_unpack_expired_value_returns_missing(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        with patch(
            "nova.cache.backends.memcached.time.time",
            side_effect=[100.0, 105.0],
        ):
            payload = backend._pack(
                "value",
                5,
            )
            result = backend._unpack(payload)

        assert result is _MISSING

    def test_unpack_invalid_serialized_payload_returns_missing(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        serializer = PickleSerializer()

        invalid_payloads = [
            serializer.dumps("value"),
            serializer.dumps((None,)),
            serializer.dumps((None, "value", "extra")),
            serializer.dumps(("invalid-expiration", "value")),
        ]

        for payload in invalid_payloads:
            assert backend._unpack(payload) != "value"

    def test_get_returns_default_for_missing_key(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.get("missing") is None
        assert backend.get("missing", "fallback") == "fallback"

    def test_set_and_get_round_trip(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        backend.set(
            "key",
            {"value": 42},
        )

        assert backend.get("key") == {"value": 42}

    def test_set_passes_zero_expiration_without_ttl(self) -> None:
        client = FakeMemcachedClient()
        backend = MemcachedCacheBackend(client=client)

        backend.set(
            "key",
            "value",
        )

        assert len(client.set_calls) == 1

        key, payload, expire = client.set_calls[0]

        assert key == "key"
        assert isinstance(payload, bytes)
        assert expire == 0

    def test_set_passes_rounded_ttl_to_client(self) -> None:
        client = FakeMemcachedClient()
        backend = MemcachedCacheBackend(client=client)

        with patch(
            "nova.cache.backends.memcached.time.time",
            return_value=100.0,
        ):
            backend.set(
                "key",
                "value",
                ttl=1.1,
            )

        assert len(client.set_calls) == 1

        _, _, expire = client.set_calls[0]

        assert expire == 2

    def test_set_supports_timedelta_ttl(self) -> None:
        client = FakeMemcachedClient()
        backend = MemcachedCacheBackend(client=client)

        backend.set(
            "key",
            "value",
            ttl=timedelta(seconds=3.1),
        )

        assert client.set_calls[0][2] == 4

    def test_delete_returns_true_for_existing_key(self) -> None:
        client = FakeMemcachedClient()
        backend = MemcachedCacheBackend(client=client)

        backend.set("key", "value")

        assert backend.delete("key") is True
        assert backend.get("key") is None

    def test_delete_returns_false_for_missing_key(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        assert backend.delete("missing") is False

    def test_clear_flushes_all_entries(self) -> None:
        client = FakeMemcachedClient()
        backend = MemcachedCacheBackend(client=client)

        backend.set("first", 1)
        backend.set("second", 2)

        backend.clear()

        assert client.flush_calls == 1
        assert backend.get("first") is None
        assert backend.get("second") is None

    def test_get_many_returns_values_and_none_for_missing_keys(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        backend.set("first", 1)
        backend.set("third", 3)

        result = backend.get_many(
            ["first", "second", "third"],
        )

        assert result == {
            "first": 1,
            "second": None,
            "third": 3,
        }

    def test_set_many_stores_all_values(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        backend.set_many(
            {
                "first": 1,
                "second": 2,
                "third": 3,
            },
        )

        assert backend.get_many(
            ["first", "second", "third"],
        ) == {
            "first": 1,
            "second": 2,
            "third": 3,
        }

    def test_set_many_applies_same_ttl_to_all_values(self) -> None:
        client = FakeMemcachedClient()
        backend = MemcachedCacheBackend(client=client)

        backend.set_many(
            {
                "first": 1,
                "second": 2,
            },
            ttl=2.1,
        )

        assert [call[2] for call in client.set_calls] == [3, 3]

    def test_delete_many_returns_number_of_deleted_keys(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        backend.set("first", 1)
        backend.set("third", 3)

        deleted = backend.delete_many(
            ["first", "second", "third", "missing"],
        )

        assert deleted == 2

    def test_delete_many_deletes_only_existing_keys(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        backend.set("first", 1)
        backend.set("second", 2)

        backend.delete_many(
            ["first", "missing"],
        )

        assert backend.get("first") is None
        assert backend.get("second") == 2

    def test_complex_values_round_trip_through_pickle(self) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        value = {
            "list": [1, 2, 3],
            "nested": {
                "enabled": True,
                "value": None,
            },
            "tuple": ("a", "b"),
        }

        backend.set(
            "complex",
            value,
        )

        assert backend.get("complex") == value

    def test_cached_none_value_is_distinguished_from_missing_key_with_default(
        self,
    ) -> None:
        backend = MemcachedCacheBackend(
            client=FakeMemcachedClient(),
        )

        backend.set(
            "cached-none",
            None,
        )

        assert backend.get("cached-none") is None
        assert backend.get("missing", "fallback") == "fallback"

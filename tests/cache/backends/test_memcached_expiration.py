"""Portable Memcached deadlines and rolling-upgrade compatibility."""

from datetime import timedelta
from unittest.mock import patch

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend, PickleSerializer
from tests.cache.backends.test_memcached import FakeMemcachedClient

LIMIT = 30 * 24 * 60 * 60
NOW = 1_800_000_000.25


@pytest.mark.parametrize(
    ("ttl", "expected"),
    [
        (None, 0),
        (LIMIT - 0.1, LIMIT),
        (LIMIT, LIMIT),
        (LIMIT + 0.1, 1_802_592_001),
        (LIMIT + 1, 1_802_592_002),
        (timedelta(days=31), 1_802_678_401),
        (timedelta(days=365), 1_831_536_001),
    ],
)
@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_server_expiration_uses_protocol_boundary(ttl, expected, bulk):
    client = FakeMemcachedClient()
    backend = MemcachedCacheBackend(client=client)
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW):
        if bulk:
            backend.set_many({"a": 1, "b": 2}, ttl=ttl)
        else:
            backend.set("a", 1, ttl=ttl)
    assert [call[2] for call in client.set_calls] == [expected] * (2 if bulk else 1)


@pytest.mark.parametrize("reader_monotonic", [5.0, 90_000_000.0])
def test_readers_with_different_monotonic_origins_share_deadline(reader_monotonic):
    client = FakeMemcachedClient()
    writer = MemcachedCacheBackend(client=client)
    reader = MemcachedCacheBackend(client=client)
    with (
        patch("nova.cache.backends.memcached.time.time", return_value=NOW),
        patch("nova.cache.backends.memcached.time.monotonic", return_value=1000.0),
    ):
        writer.set("key", "value", ttl=5)
    with (
        patch("nova.cache.backends.memcached.time.time", return_value=NOW + 4),
        patch("nova.cache.backends.memcached.time.monotonic", return_value=reader_monotonic),
    ):
        assert reader.get("key") == "value"
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW + 5):
        assert reader.get("key", "MISS") == "MISS"


def test_fractional_deadline_boundary_without_sleep():
    client = FakeMemcachedClient()
    backend = MemcachedCacheBackend(client=client)
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW):
        backend.set("key", "value", ttl=0.125)
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW + 0.124):
        assert backend.get("key") == "value"
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW + 0.125):
        assert backend.get("key", "MISS") == "MISS"


@pytest.mark.parametrize("bulk", [False, True])
def test_persistent_overwrite_removes_payload_and_server_deadlines(bulk):
    client = FakeMemcachedClient()
    backend = MemcachedCacheBackend(client=client)
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW):
        backend.set("key", "old", ttl=5)
        if bulk:
            backend.set_many({"key": "new"}, ttl=None)
        else:
            backend.set("key", "new", ttl=None)
    assert client.set_calls[-1][2] == 0
    with patch("nova.cache.backends.memcached.time.time", return_value=NOW + 1_000_000):
        assert backend.get("key") == "new"


@pytest.mark.parametrize("value", [None, "value", {"nested": [1, 2]}])
def test_legacy_persistent_payload_still_readable(value):
    client = FakeMemcachedClient()
    client.data["legacy"] = PickleSerializer().dumps((None, value))
    assert MemcachedCacheBackend(client=client).get("legacy", "MISS") == value


@pytest.mark.parametrize("deadline", [0.0, 1e20])
def test_legacy_monotonic_payload_is_a_miss_without_deleting_key(deadline):
    client = FakeMemcachedClient()
    client.data["legacy"] = PickleSerializer().dumps((deadline, "old"))
    backend = MemcachedCacheBackend(client=client)
    sentinel = object()
    assert backend.get("legacy", sentinel) is sentinel
    # No read-time delete that could race with another client's replacement.
    assert client.delete_calls == []
    assert backend.get_many(["legacy"]) == {"legacy": None}


@pytest.mark.parametrize(
    "envelope",
    [
        ("unknown-version", None, "value"),
        ("nova:memcached:v2", float("nan"), "value"),
        ("nova:memcached:v2", float("inf"), "value"),
        ("nova:memcached:v2", float("-inf"), "value"),
        ("nova:memcached:v2", True, "value"),
        ("nova:memcached:v2", "invalid", "value"),
    ],
)
def test_invalid_versioned_deadline_is_a_miss(envelope):
    client = FakeMemcachedClient()
    client.data["key"] = PickleSerializer().dumps(envelope)
    backend = MemcachedCacheBackend(client=client)
    assert backend.get("key", "MISS") == "MISS"

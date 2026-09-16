"""Real Redis tests, enabled by NOVA_TEST_REDIS_URL.

Use only a disposable test server. Each test owns a unique namespace.
No FLUSHDB, FLUSHALL or global server reconfiguration is performed.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from redis import Redis

from nova.cache.backends.redis import RedisCacheBackend
from nova.core.exceptions import NovaCacheError


@dataclass(frozen=True)
class RedisCase:
    client: Redis
    prefix: str
    backend: RedisCacheBackend


@pytest.fixture
def redis_case() -> Iterator[RedisCase]:
    url = os.environ.get("NOVA_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set NOVA_TEST_REDIS_URL to run real Redis integration tests")

    # Keep responses binary: the backend stores pickled values.
    client = Redis.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    prefix = f"nova-it:{uuid4().hex}"
    try:
        # Connection/authentication failures deliberately fail this fixture.
        assert client.ping() is True
        yield RedisCase(
            client=client,
            prefix=prefix,
            backend=RedisCacheBackend(client=client, key_prefix=prefix),
        )
    finally:
        try:
            # Snapshot before deletion; do not mutate the scan iteration.
            keys = list(client.scan_iter(match=f"{prefix}:*", count=200))
            for offset in range(0, len(keys), 200):
                client.delete(*keys[offset : offset + 200])
        finally:
            client.close()


def test_python_values_roundtrip(redis_case: RedisCase) -> None:
    payload = {
        "decimal": Decimal("123.45"),
        "date": date(2026, 9, 15),
        "uuid": uuid4(),
        "bytes": b"\x00\xff",
        "nested": [False, "", 0, {"text": "Нова"}],
    }
    backend = redis_case.backend
    backend.set("value", payload)
    assert backend.get("value") == payload
    sentinel = object()
    backend.set("none", None)
    assert backend.get("none", sentinel) is None
    assert backend.get("missing", sentinel) is sentinel


def test_bulk_and_delete_counts(redis_case: RedisCase) -> None:
    backend = redis_case.backend
    backend.set_many({"a": 1, "b": False, "c": None})
    assert backend.get_many(["a", "b", "c", "absent"]) == {
        "a": 1,
        "b": False,
        "c": None,
        "absent": None,
    }
    assert backend.delete_many(["a", "a", "absent"]) == 1
    assert backend.get("b") is False
    assert backend.delete("b") is True
    assert backend.delete("b") is False


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_real_expiration(redis_case: RedisCase, bulk: bool) -> None:
    backend = redis_case.backend
    keys = ["a", "b"] if bulk else ["a"]
    ttl = timedelta(seconds=1)
    if bulk:
        backend.set_many(dict.fromkeys(keys, "temporary"), ttl=ttl)
    else:
        backend.set("a", "temporary", ttl=ttl)

    for key in keys:
        assert backend.get(key) == "temporary"
        assert 0 < redis_case.client.pttl(f"{redis_case.prefix}:{key}") <= 1000

    deadline = time.monotonic() + 5
    sentinel = object()
    while True:
        values = [backend.get(key, sentinel) for key in keys]
        if all(value is sentinel for value in values):
            break
        assert time.monotonic() < deadline, "Redis keys did not expire within 5 seconds"
        time.sleep(0.02)
    assert backend.delete_many(keys) == 0


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_persistent_overwrite_removes_ttl(redis_case: RedisCase, bulk: bool) -> None:
    backend = redis_case.backend
    backend.set("key", 1, ttl=60)
    assert redis_case.client.pttl(f"{redis_case.prefix}:key") > 0
    if bulk:
        backend.set_many({"key": 2})
    else:
        backend.set("key", 2)
    assert backend.get("key") == 2
    assert redis_case.client.pttl(f"{redis_case.prefix}:key") == -1


@pytest.mark.parametrize(
    ("suffix", "neighbor"),
    [
        ("tenant*", "tenant-other"),
        ("tenant?", "tenantX"),
        ("tenant[ab]", "tenanta"),
        (r"tenant\a", "tenanta"),
    ],
)
def test_clear_escapes_literal_prefix(
    redis_case: RedisCase,
    suffix: str,
    neighbor: str,
) -> None:
    own = RedisCacheBackend(
        client=redis_case.client,
        key_prefix=f"{redis_case.prefix}:{suffix}",
    )
    other = RedisCacheBackend(
        client=redis_case.client,
        key_prefix=f"{redis_case.prefix}:{neighbor}",
    )
    own.set("key", 1)
    other.set("key", 2)

    own.clear()

    assert other.get("key") == 2, "clear() crossed a namespace boundary"
    assert own.get("key") is None


def test_clear_large_namespace_preserves_neighbors(redis_case: RedisCase) -> None:
    own = RedisCacheBackend(
        client=redis_case.client,
        key_prefix=f"{redis_case.prefix}:own",
    )
    other = RedisCacheBackend(
        client=redis_case.client,
        key_prefix=f"{redis_case.prefix}:own-other",
    )
    values = {f"key-{number}": number for number in range(1200)}
    own.set_many(values)
    other.set("keep", "preserved")
    assert own.get_many(list(values)) == values

    own.clear()

    assert all(value is None for value in own.get_many(list(values)).values())
    assert other.get("keep") == "preserved"
    assert list(redis_case.client.scan_iter(match=f"{redis_case.prefix}:own:*")) == []


def test_clear_without_prefix_is_rejected(redis_case: RedisCase) -> None:
    backend = RedisCacheBackend(client=redis_case.client, key_prefix="")
    key = f"{redis_case.prefix}:keep"
    backend.set(key, "preserved")
    with pytest.raises(NovaCacheError, match="key_prefix"):
        backend.clear()
    assert backend.get(key) == "preserved"


def test_server_memory_stats_are_available(redis_case: RedisCase) -> None:
    stats = redis_case.backend.stats()
    assert stats["backend"] == "redis"
    assert isinstance(stats["used_memory"], str)
    assert stats["used_memory"] != "Unknown"
    # Current implementation reports database-wide counts, not prefix counts.
    assert isinstance(stats["keys"], int)
    assert stats["keys"] >= 0

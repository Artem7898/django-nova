"""Real asynchronous Redis tests, enabled by NOVA_TEST_REDIS_URL.

Use only a disposable test server. Each test owns a unique namespace.
No FLUSHDB, FLUSHALL or global server reconfiguration is performed.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from nova.cache.backends.redis_backend import AsyncRedisCacheBackend
from nova.core.exceptions import NovaCacheError

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class RedisCase:
    client: Redis
    prefix: str
    backend: AsyncRedisCacheBackend


@pytest_asyncio.fixture
async def redis_case() -> AsyncIterator[RedisCase]:
    url = os.environ.get("NOVA_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set NOVA_TEST_REDIS_URL to run real Redis integration tests")
    client = Redis.from_url(url, decode_responses=False, socket_connect_timeout=3, socket_timeout=3)
    prefix = f"nova-it:{uuid4().hex}"
    try:
        assert await client.ping() is True
        yield RedisCase(
            client=client,
            prefix=prefix,
            backend=AsyncRedisCacheBackend(client=client, key_prefix=prefix),
        )
    finally:
        try:
            keys = [key async for key in client.scan_iter(match=f"{prefix}:*", count=200)]
            for offset in range(0, len(keys), 200):
                await client.delete(*keys[offset : offset + 200])
        finally:
            await client.aclose()


async def test_python_values_roundtrip(redis_case: RedisCase) -> None:
    payload = {
        "decimal": Decimal("123.45"),
        "date": date(2026, 9, 15),
        "uuid": uuid4(),
        "bytes": b"\x00\xff",
        "nested": [False, "", 0, {"text": "Нова"}],
    }
    backend = redis_case.backend
    await backend.set("value", payload)
    assert await backend.get("value") == payload
    sentinel = object()
    await backend.set("none", None)
    assert await backend.get("none", sentinel) is None
    assert await backend.get("missing", sentinel) is sentinel


async def test_bulk_and_delete_counts(redis_case: RedisCase) -> None:
    backend = redis_case.backend
    await backend.set_many({"a": 1, "b": False, "c": None})
    assert await backend.get_many(["a", "b", "c", "absent"]) == {
        "a": 1,
        "b": False,
        "c": None,
        "absent": None,
    }
    assert await backend.delete_many(["a", "a", "absent"]) == 1
    assert await backend.get("b") is False
    assert await backend.delete("b") is True
    assert await backend.delete("b") is False


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
async def test_real_expiration(redis_case: RedisCase, bulk: bool) -> None:
    backend = redis_case.backend
    keys = ["a", "b"] if bulk else ["a"]
    ttl = timedelta(seconds=1)
    if bulk:
        await backend.set_many(dict.fromkeys(keys, "temporary"), ttl=ttl)
    else:
        await backend.set("a", "temporary", ttl=ttl)
    for key in keys:
        assert await backend.get(key) == "temporary"
        assert 0 < await redis_case.client.pttl(f"{redis_case.prefix}:{key}") <= 1000
    deadline = time.monotonic() + 5
    sentinel = object()
    while True:
        values = [await backend.get(key, sentinel) for key in keys]
        if all(value is sentinel for value in values):
            break
        assert time.monotonic() < deadline, "Redis keys did not expire within 5 seconds"
        await asyncio.sleep(0.02)
    assert await backend.delete_many(keys) == 0


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
async def test_persistent_overwrite_removes_ttl(redis_case: RedisCase, bulk: bool) -> None:
    backend = redis_case.backend
    await backend.set("key", 1, ttl=60)
    assert await redis_case.client.pttl(f"{redis_case.prefix}:key") > 0
    if bulk:
        await backend.set_many({"key": 2})
    else:
        await backend.set("key", 2)
    assert await backend.get("key") == 2
    assert await redis_case.client.pttl(f"{redis_case.prefix}:key") == -1


@pytest.mark.parametrize(
    ("suffix", "neighbor"),
    [
        ("tenant*", "tenant-other"),
        ("tenant?", "tenantX"),
        ("tenant[ab]", "tenanta"),
        ("tenant\\a", "tenanta"),
    ],
)
async def test_clear_escapes_literal_prefix(
    redis_case: RedisCase, suffix: str, neighbor: str
) -> None:
    own = AsyncRedisCacheBackend(
        client=redis_case.client, key_prefix=f"{redis_case.prefix}:{suffix}"
    )
    other = AsyncRedisCacheBackend(
        client=redis_case.client, key_prefix=f"{redis_case.prefix}:{neighbor}"
    )
    await own.set("key", 1)
    await other.set("key", 2)
    await own.clear()
    assert await other.get("key") == 2, "clear() crossed a namespace boundary"
    assert await own.get("key") is None


async def test_clear_large_namespace_preserves_neighbors(redis_case: RedisCase) -> None:
    own = AsyncRedisCacheBackend(client=redis_case.client, key_prefix=f"{redis_case.prefix}:own")
    other = AsyncRedisCacheBackend(
        client=redis_case.client, key_prefix=f"{redis_case.prefix}:own-other"
    )
    values = {f"key-{number}": number for number in range(1200)}
    await own.set_many(values)
    await other.set("keep", "preserved")
    assert await own.get_many(list(values)) == values
    await own.clear()
    assert all(value is None for value in (await own.get_many(list(values))).values())
    assert await other.get("keep") == "preserved"
    assert [
        key async for key in redis_case.client.scan_iter(match=f"{redis_case.prefix}:own:*")
    ] == []


async def test_clear_without_prefix_is_rejected(redis_case: RedisCase) -> None:
    backend = AsyncRedisCacheBackend(client=redis_case.client, key_prefix="")
    key = f"{redis_case.prefix}:keep"
    await backend.set(key, "preserved")
    with pytest.raises(NovaCacheError, match="key_prefix"):
        await backend.clear()
    assert await backend.get(key) == "preserved"

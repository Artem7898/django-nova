"""Async Redis safety regressions; no external server required."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
import pytest_asyncio
from redis.exceptions import ConnectionError as RedisConnectionError

from nova.cache.backends.redis_backend import AsyncRedisCacheBackend
from nova.core.exceptions import NovaCacheError

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    redis = fakeredis.FakeAsyncRedis(decode_responses=False)
    try:
        yield redis
    finally:
        await redis.aclose()


@pytest.mark.parametrize(
    ("prefix", "neighbor"),
    [
        ("nova", "nova-other"),
        ("tenant*", "tenant-other"),
        ("tenant?", "tenantX"),
        ("tenant[ab]", "tenanta"),
        (r"tenant\a", "tenanta"),
    ],
)
async def test_clear_preserves_neighbor(client, prefix, neighbor):
    own = AsyncRedisCacheBackend(client=client, key_prefix=prefix)
    other = AsyncRedisCacheBackend(client=client, key_prefix=neighbor)
    await own.set("key", 1)
    await other.set("key", 2)
    await own.clear()
    assert await other.get("key") == 2
    assert await own.get("key") is None


async def test_empty_prefix_refuses_clear(client):
    backend = AsyncRedisCacheBackend(client=client, key_prefix="")
    await backend.set("key", 1)
    with pytest.raises(NovaCacheError, match="key_prefix"):
        await backend.clear()
    assert await backend.get("key") == 1


async def test_clear_continues_after_empty_scan_page():
    client = MagicMock()
    client.scan = AsyncMock(side_effect=[(7, []), (9, [b"nova:a"]), (0, [b"nova:b"])])
    client.delete = AsyncMock(return_value=1)
    await AsyncRedisCacheBackend(client=client).clear()
    assert [call.kwargs["cursor"] for call in client.scan.await_args_list] == [0, 7, 9]
    assert [call.args for call in client.delete.await_args_list] == [
        (b"nova:a",),
        (b"nova:b",),
    ]
    client.flushdb.assert_not_called()
    client.flushall.assert_not_called()


async def test_empty_bulk_does_not_contact_redis():
    client = MagicMock()
    backend = AsyncRedisCacheBackend(client=client)
    assert await backend.get_many([]) == {}
    await backend.set_many({})
    assert await backend.delete_many([]) == 0
    assert client.mock_calls == []


async def test_bulk_delete_counts_unique_existing_keys(client):
    backend = AsyncRedisCacheBackend(client=client)
    await backend.set_many({"a": 1, "b": 2})
    assert await backend.delete_many(["a", "a", "missing"]) == 1
    assert await backend.get_many(["a", "b"]) == {"a": None, "b": 2}


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
@pytest.mark.parametrize("ttl", [30, 30.5, timedelta(seconds=30)])
async def test_ttl_and_persistent_overwrite(client, bulk, ttl):
    backend = AsyncRedisCacheBackend(client=client)
    if bulk:
        await backend.set_many({"a": 1, "b": 2}, ttl=ttl)
    else:
        await backend.set("a", 1, ttl=ttl)
    keys = ["a", "b"] if bulk else ["a"]
    limit = int((ttl.total_seconds() if isinstance(ttl, timedelta) else ttl) * 1000)
    for key in keys:
        assert 0 < await client.pttl(f"nova:{key}") <= limit
    if bulk:
        await backend.set_many(dict.fromkeys(keys, 3))
    else:
        await backend.set("a", 3)
    for key in keys:
        assert await backend.get(key) == 3
        assert await client.pttl(f"nova:{key}") == -1


OPERATIONS = ("get", "set", "delete", "clear", "get_many", "set_many", "delete_many")


async def invoke(backend, operation):
    if operation == "set":
        return await backend.set("key", 1)
    if operation == "set_many":
        return await backend.set_many({"key": 1})
    if operation in {"get_many", "delete_many"}:
        return await getattr(backend, operation)(["key"])
    if operation == "clear":
        return await backend.clear()
    return await getattr(backend, operation)("key")


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_connection_error_preserves_cause(operation):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    try:
        server.connected = False
        with pytest.raises(NovaCacheError) as caught:
            await invoke(AsyncRedisCacheBackend(client=client), operation)
        assert isinstance(caught.value.__cause__, RedisConnectionError)
    finally:
        await client.aclose()


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_cancellation_propagates(operation):
    client = MagicMock()
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def wait_for_cancellation(*args, **kwargs):
        started.set()
        await blocker.wait()

    command = {
        "get_many": "mget",
        "delete_many": "delete",
        "clear": "scan",
    }.get(operation, operation)
    if operation == "set_many":
        client.pipeline.return_value.execute = AsyncMock(side_effect=wait_for_cancellation)
    else:
        setattr(client, command, AsyncMock(side_effect=wait_for_cancellation))

    task = asyncio.create_task(invoke(AsyncRedisCacheBackend(client=client), operation))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_programming_error_is_not_wrapped(operation):
    client = MagicMock()
    error = TypeError("application error")
    command = {
        "get_many": "mget",
        "delete_many": "delete",
        "clear": "scan",
    }.get(operation, operation)
    if operation == "set_many":
        client.pipeline.return_value.execute = AsyncMock(side_effect=error)
    else:
        setattr(client, command, AsyncMock(side_effect=error))
    with pytest.raises(TypeError) as caught:
        await invoke(AsyncRedisCacheBackend(client=client), operation)
    assert caught.value is error

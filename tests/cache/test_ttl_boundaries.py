"""Non-positive TTL removes keys without serialization or collateral eviction."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import fakeredis
import pytest
from django.core.cache.backends.locmem import LocMemCache

from nova.cache.backends.asyncio_backend import AsyncIOCacheBackend
from nova.cache.backends.django_cache import DjangoCacheBackend
from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.null import NullCacheBackend
from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.backends.redis_backend import AsyncRedisCacheBackend

NON_POSITIVE = [0, -1, -0.0001, timedelta(0), timedelta(microseconds=-1)]
OPERATIONS = [False, True]


class MemcachedStub:
    """Byte store: deliberately leaves expiration policy to the adapter."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, expire=0):
        self.data[key] = value
        return True

    def delete(self, key):
        return self.data.pop(key, None) is not None

    def flush_all(self):
        self.data.clear()


@pytest.fixture(params=["memory", "django", "memcached", "redis"])
def backend(request, monkeypatch):
    name = request.param
    client = None
    if name == "memory":
        result = MemoryCacheBackend()
    elif name == "django":
        cache = LocMemCache(uuid4().hex, {})
        monkeypatch.setattr("nova.cache.backends.django_cache.caches", {"ttl": cache})
        result = DjangoCacheBackend(alias="ttl")
    elif name == "memcached":
        result = MemcachedCacheBackend(client=MemcachedStub())
    else:
        client = fakeredis.FakeRedis()
        result = RedisCacheBackend(client=client)
    try:
        yield result
    finally:
        result.clear()
        if client is not None:
            client.close()


@pytest.mark.parametrize("ttl", NON_POSITIVE)
@pytest.mark.parametrize("bulk", OPERATIONS, ids=["set", "set-many"])
def test_non_positive_removes_existing_and_missing(backend, ttl, bulk):
    backend.set("existing", "old", ttl=60)
    backend.set("neighbor", "preserved", ttl=60)
    if bulk:
        backend.set_many({"existing": "new", "missing": "new"}, ttl=ttl)
    else:
        backend.set("existing", "new", ttl=ttl)
        backend.set("missing", "new", ttl=ttl)
    sentinel = object()
    for key in ("existing", "missing"):
        assert backend.get(key, sentinel) is sentinel
        assert backend.delete(key) is False
    assert backend.get("neighbor") == "preserved"


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", NON_POSITIVE)
@pytest.mark.parametrize("bulk", OPERATIONS, ids=["set", "set-many"])
async def test_async_redis_non_positive_removes_keys(ttl, bulk):
    client = fakeredis.FakeAsyncRedis()
    try:
        backend = AsyncRedisCacheBackend(client=client)
        await backend.set("existing", "old", ttl=60)
        await backend.set("neighbor", "preserved", ttl=60)
        if bulk:
            await backend.set_many({"existing": "new", "missing": "new"}, ttl=ttl)
        else:
            await backend.set("existing", "new", ttl=ttl)
            await backend.set("missing", "new", ttl=ttl)
        sentinel = object()
        for key in ("existing", "missing"):
            assert await backend.get(key, sentinel) is sentinel
            assert await backend.delete(key) is False
        assert await backend.get("neighbor") == "preserved"
    finally:
        await client.aclose()


@pytest.mark.parametrize("bulk", OPERATIONS)
def test_zero_ttl_does_not_evict_unrelated_memory_key(bulk):
    backend = MemoryCacheBackend(maxsize=1)
    backend.set("neighbor", "preserved", ttl=60)
    if bulk:
        backend.set_many({"expired": "value"}, ttl=0)
    else:
        backend.set("expired", "value", ttl=0)
    assert backend.get("neighbor") == "preserved"
    assert backend.size() == 1


@pytest.mark.parametrize("bulk", OPERATIONS)
def test_expired_write_does_not_serialize(backend, bulk):
    class CannotSerialize:
        def __reduce__(self):
            raise AssertionError("Expired writes must not serialize values")

    backend.set("key", "old", ttl=60)
    if bulk:
        backend.set_many({"key": CannotSerialize()}, ttl=0)
    else:
        backend.set("key", CannotSerialize(), ttl=0)
    assert backend.get("key") is None


@pytest.mark.parametrize("bulk", OPERATIONS)
@pytest.mark.parametrize("ttl", [0.000001, timedelta(microseconds=1)])
def test_positive_submillisecond_redis_ttl_still_writes(bulk, ttl):
    client = MagicMock()
    pipe = client.pipeline.return_value.__enter__.return_value
    backend = RedisCacheBackend(client=client)
    if bulk:
        backend.set_many({"key": 1}, ttl=ttl)
        assert pipe.set.call_args.kwargs["px"] == 1
        pipe.execute.assert_called_once()
    else:
        backend.set("key", 1, ttl=ttl)
        assert client.set.call_args.kwargs["px"] == 1
    client.delete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", OPERATIONS)
@pytest.mark.parametrize("ttl", [0.000001, timedelta(microseconds=1)])
async def test_positive_submillisecond_async_redis_ttl_still_writes(bulk, ttl):
    client = MagicMock()
    client.set = AsyncMock()
    pipe = client.pipeline.return_value
    pipe.execute = AsyncMock()
    backend = AsyncRedisCacheBackend(client=client)
    if bulk:
        await backend.set_many({"key": 1}, ttl=ttl)
        assert pipe.set.call_args.kwargs["px"] == 1
        pipe.execute.assert_awaited_once()
    else:
        await backend.set("key", 1, ttl=ttl)
        assert client.set.await_args.kwargs["px"] == 1
    client.delete.assert_not_called()


def test_none_keeps_memory_constructor_default(monkeypatch):
    backend = MemoryCacheBackend(ttl=10)
    now = [100.0]
    monkeypatch.setattr(backend, "_now", lambda: now[0])
    backend.set("key", 1, ttl=None)
    now[0] = 109
    assert backend.get("key") == 1
    now[0] = 110
    assert backend.get("key") is None


@pytest.mark.parametrize("ttl", NON_POSITIVE)
def test_null_backend_still_ignores_ttl(ttl):
    backend = NullCacheBackend()
    backend.set("a", 1, ttl=ttl)
    backend.set_many({"b": 2}, ttl=ttl)
    assert backend.get_many(["a", "b"]) == {"a": 1, "b": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", NON_POSITIVE)
async def test_asyncio_backend_still_ignores_ttl(ttl):
    backend = AsyncIOCacheBackend()
    await backend.set("a", 1, ttl=ttl)
    await backend.set_many({"b": 2}, ttl=ttl)
    assert await backend.get_many(["a", "b"]) == {"a": 1, "b": 2}

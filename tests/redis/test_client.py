"""
Contract tests for unified Redis client infrastructure.
"""

from __future__ import annotations

from typing import Any

import pytest

from nova.core.exceptions import NovaCacheError
from nova.redis import client as client_module


class FakePool:
    pass


class FakeSyncRedis:
    def __init__(self, connection_pool: Any | None = None) -> None:
        self.connection_pool = connection_pool


class FakeAsyncRedis:
    def __init__(self, connection_pool: Any | None = None) -> None:
        self.connection_pool = connection_pool


class FakeRedisModule:
    Redis = FakeSyncRedis


class FakeAsyncRedisModule:
    Redis = FakeAsyncRedis


@pytest.fixture(autouse=True)
def reset_redis_clients() -> Any:
    client_module.set_sync_redis_client(None)
    client_module.set_async_redis_client(None)

    yield

    client_module.set_sync_redis_client(None)
    client_module.set_async_redis_client(None)


def test_get_redis_client_returns_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "redis", FakeRedisModule)
    monkeypatch.setattr(client_module, "_get_sync_pool", lambda: FakePool())

    first = client_module.get_redis_client()
    second = client_module.get_redis_client()

    assert first is second
    assert isinstance(first, FakeSyncRedis)


def test_get_redis_client_uses_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()

    monkeypatch.setattr(client_module, "redis", FakeRedisModule)
    monkeypatch.setattr(client_module, "_get_sync_pool", lambda: pool)

    client = client_module.get_redis_client()

    assert client.connection_pool is pool


def test_get_redis_client_raises_when_redis_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "redis", None)

    with pytest.raises(NovaCacheError):
        client_module.get_redis_client()


def test_get_async_redis_client_returns_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "aioredis", FakeAsyncRedisModule)
    monkeypatch.setattr(client_module, "_get_async_pool", lambda: FakePool())

    first = client_module.get_async_redis_client()
    second = client_module.get_async_redis_client()

    assert first is second
    assert isinstance(first, FakeAsyncRedis)


def test_get_async_redis_client_uses_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()

    monkeypatch.setattr(client_module, "aioredis", FakeAsyncRedisModule)
    monkeypatch.setattr(client_module, "_get_async_pool", lambda: pool)

    client = client_module.get_async_redis_client()

    assert client.connection_pool is pool


def test_get_async_redis_client_raises_when_redis_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "aioredis", None)

    with pytest.raises(NovaCacheError):
        client_module.get_async_redis_client()


def test_set_sync_redis_client_overrides_global_client() -> None:
    fake = FakeSyncRedis()

    client_module.set_sync_redis_client(fake)

    assert client_module.get_redis_client() is fake


def test_set_async_redis_client_overrides_global_client() -> None:
    fake = FakeAsyncRedis()

    client_module.set_async_redis_client(fake)

    assert client_module.get_async_redis_client() is fake


def test_reset_redis_clients() -> None:
    client_module.set_sync_redis_client(FakeSyncRedis())
    client_module.set_async_redis_client(FakeAsyncRedis())

    client_module.reset_redis_clients()

    assert client_module._sync_client is None
    assert client_module._async_client is None
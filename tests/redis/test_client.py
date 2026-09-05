"""Tests for Nova's unified Redis client infrastructure."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import nova.redis.client as redis_client
from nova.core.exceptions import NovaCacheError


@pytest.fixture(autouse=True)
def reset_clients() -> None:
    """Keep global Redis client state isolated between tests."""
    redis_client.reset_redis_clients()


def test_get_redis_client_returns_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronous Redis client is initialized only once."""
    first = MagicMock(name="sync-redis")
    second = MagicMock(name="sync-redis-second")

    redis_module = SimpleNamespace(
        Redis=MagicMock(side_effect=[first, second]),
    )

    pool = MagicMock(name="sync-pool")

    monkeypatch.setattr(redis_client, "redis", redis_module)
    monkeypatch.setattr(redis_client, "_get_sync_pool", lambda: pool)

    result_one = redis_client.get_redis_client()
    result_two = redis_client.get_redis_client()

    assert result_one is first
    assert result_two is first
    assert result_one is result_two

    redis_module.Redis.assert_called_once_with(connection_pool=pool)


def test_get_async_redis_client_returns_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asynchronous Redis client is initialized only once."""
    first = MagicMock(name="async-redis")
    second = MagicMock(name="async-redis-second")

    redis_module = SimpleNamespace(
        Redis=MagicMock(side_effect=[first, second]),
    )

    pool = MagicMock(name="async-pool")

    monkeypatch.setattr(redis_client, "aioredis", redis_module)
    monkeypatch.setattr(redis_client, "_get_async_pool", lambda: pool)

    result_one = redis_client.get_async_redis_client()
    result_two = redis_client.get_async_redis_client()

    assert result_one is first
    assert result_two is first
    assert result_one is result_two

    redis_module.Redis.assert_called_once_with(connection_pool=pool)


def test_get_redis_client_raises_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync Redis access fails explicitly when redis is unavailable."""
    monkeypatch.setattr(redis_client, "redis", None)

    with pytest.raises(NovaCacheError, match="Redis package is not installed"):
        redis_client.get_redis_client()


def test_get_async_redis_client_raises_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async Redis access fails explicitly when redis.asyncio is unavailable."""
    monkeypatch.setattr(redis_client, "aioredis", None)

    with pytest.raises(
        NovaCacheError,
        match="Async redis package is not installed",
    ):
        redis_client.get_async_redis_client()


def test_get_sync_pool_raises_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync pool creation respects the optional dependency boundary."""
    monkeypatch.setattr(redis_client, "redis", None)

    with pytest.raises(NovaCacheError, match="Redis package is not installed"):
        redis_client._get_sync_pool()


def test_get_async_pool_raises_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async pool creation respects the optional dependency boundary."""
    monkeypatch.setattr(redis_client, "aioredis", None)

    with pytest.raises(
        NovaCacheError,
        match="Async redis package is not installed",
    ):
        redis_client._get_async_pool()


def test_get_sync_pool_uses_nova_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync pool receives the configured Nova Redis settings."""
    pool = MagicMock(name="sync-pool")
    from_url = MagicMock(return_value=pool)

    redis_module = SimpleNamespace(
        ConnectionPool=SimpleNamespace(from_url=from_url),
    )

    settings = SimpleNamespace(
        redis_url="redis://example:6379/1",
        redis_max_connections=17,
        redis_socket_timeout=2.5,
        redis_socket_connect_timeout=3.5,
        redis_retry_on_timeout=True,
        redis_health_check_interval=30,
    )

    monkeypatch.setattr(redis_client, "redis", redis_module)
    monkeypatch.setattr(redis_client, "nova_settings", settings)

    result = redis_client._get_sync_pool()

    assert result is pool

    from_url.assert_called_once_with(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        retry_on_timeout=settings.redis_retry_on_timeout,
        health_check_interval=settings.redis_health_check_interval,
    )


def test_get_async_pool_uses_nova_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async pool receives the configured Nova Redis settings."""
    pool = MagicMock(name="async-pool")
    from_url = MagicMock(return_value=pool)

    redis_module = SimpleNamespace(
        ConnectionPool=SimpleNamespace(from_url=from_url),
    )

    settings = SimpleNamespace(
        redis_url="redis://example:6379/2",
        redis_max_connections=23,
        redis_socket_timeout=4.5,
        redis_socket_connect_timeout=5.5,
        redis_retry_on_timeout=False,
    )

    monkeypatch.setattr(redis_client, "aioredis", redis_module)
    monkeypatch.setattr(redis_client, "nova_settings", settings)

    result = redis_client._get_async_pool()

    assert result is pool

    from_url.assert_called_once_with(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        retry_on_timeout=settings.redis_retry_on_timeout,
    )


def test_set_sync_redis_client_overrides_global() -> None:
    """Sync client can be explicitly injected."""
    client = MagicMock(name="sync-client")

    redis_client.set_sync_redis_client(client)

    assert redis_client.get_redis_client() is client


def test_set_async_redis_client_overrides_global() -> None:
    """Async client can be explicitly injected."""
    client = MagicMock(name="async-client")

    redis_client.set_async_redis_client(client)

    assert redis_client.get_async_redis_client() is client


def test_reset_redis_clients_clears_both_clients() -> None:
    """Reset clears both process-wide client references."""
    sync_client = MagicMock(name="sync-client")
    async_client = MagicMock(name="async-client")

    redis_client.set_sync_redis_client(sync_client)
    redis_client.set_async_redis_client(async_client)

    redis_client.reset_redis_clients()

    assert redis_client._sync_client is None
    assert redis_client._async_client is None


def test_close_redis_clients_closes_sync_client() -> None:
    """Synchronous lifecycle API closes only the sync client."""
    client = MagicMock(name="sync-client")
    async_client = MagicMock(name="async-client")

    redis_client.set_sync_redis_client(client)
    redis_client.set_async_redis_client(async_client)

    redis_client.close_redis_clients()

    client.close.assert_called_once_with()
    async_client.aclose.assert_not_called()

    assert redis_client._sync_client is None
    assert redis_client._async_client is async_client


def test_close_redis_clients_is_idempotent() -> None:
    """Synchronous close can safely be called repeatedly."""
    client = MagicMock(name="sync-client")

    redis_client.set_sync_redis_client(client)

    redis_client.close_redis_clients()
    redis_client.close_redis_clients()

    client.close.assert_called_once_with()
    assert redis_client._sync_client is None


def test_close_redis_clients_handles_missing_close_method(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing sync close method is isolated from the lifecycle boundary."""
    sync_client = MagicMock(name="sync-client")
    del sync_client.close

    async_client = MagicMock(name="async-client")

    redis_client.set_sync_redis_client(sync_client)
    redis_client.set_async_redis_client(async_client)

    redis_client.close_redis_clients()

    assert "does not provide a close()" in caplog.text

    assert redis_client._sync_client is None
    assert redis_client._async_client is async_client

    async_client.aclose.assert_not_called()


def test_close_redis_clients_isolates_close_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sync close failures are logged and do not escape shutdown."""
    client = MagicMock(name="sync-client")
    client.close.side_effect = RuntimeError("connection failure")

    redis_client.set_sync_redis_client(client)

    redis_client.close_redis_clients()

    client.close.assert_called_once_with()
    assert "Error closing Sync Redis client" in caplog.text
    assert redis_client._sync_client is None


@pytest.mark.asyncio
async def test_aclose_redis_clients_awaits_async_close() -> None:
    """Async lifecycle API awaits the client's aclose operation."""
    closed = False

    async def aclose() -> None:
        nonlocal closed
        closed = True

    client = MagicMock(name="async-client")
    client.aclose = aclose

    redis_client.set_async_redis_client(client)

    await redis_client.aclose_redis_clients()

    assert closed is True
    assert redis_client._async_client is None


@pytest.mark.asyncio
async def test_aclose_redis_clients_is_safe_when_no_client_exists() -> None:
    """Async close is idempotent when no client is configured."""
    redis_client.set_async_redis_client(None)

    await redis_client.aclose_redis_clients()

    assert redis_client._async_client is None


@pytest.mark.asyncio
async def test_aclose_redis_clients_preserves_client_on_failure() -> None:
    """Failed async cleanup preserves the client for retry/recovery."""

    async def aclose() -> None:
        raise RuntimeError("async close failure")

    client = MagicMock(name="async-client")
    client.aclose = aclose

    redis_client.set_async_redis_client(client)

    with pytest.raises(RuntimeError, match="async close failure"):
        await redis_client.aclose_redis_clients()

    assert redis_client._async_client is client


def test_close_redis_clients_is_safe_when_no_clients_exist() -> None:
    """Synchronous close is idempotent when no client is configured."""
    redis_client.close_redis_clients()

    assert redis_client._sync_client is None
    assert redis_client._async_client is None

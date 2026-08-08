"""
Contract tests for Redis connection pool management.
"""

from __future__ import annotations

from typing import Any

import pytest

from nova.cache.backends import pool as pool_module
from nova.cache.backends.pool import clear_redis_pool_cache, get_redis_pool


class FakeConnectionPool:
    def __init__(self, url: str, **kwargs: Any) -> None:
        self.url = url
        self.kwargs = kwargs

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> FakeConnectionPool:
        return cls(url, **kwargs)


class FakeRedisModule:
    ConnectionPool = FakeConnectionPool


@pytest.fixture(autouse=True)
def _clear_pool_cache() -> Any:
    clear_redis_pool_cache()
    yield
    clear_redis_pool_cache()


def test_empty_url_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Redis URL must not be empty"):
        get_redis_pool("")


def test_missing_redis_dependency_raises_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool_module, "REDIS_AVAILABLE", False)
    monkeypatch.setattr(pool_module, "_redis_module", None)

    with pytest.raises(ImportError, match="No Redis dependencies"):
        get_redis_pool("redis://localhost:6379/0")


def test_pool_created_with_expected_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool_module, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(pool_module, "_redis_module", FakeRedisModule)

    pool = get_redis_pool("redis://localhost:6379/0")

    assert isinstance(pool, FakeConnectionPool)
    assert pool.url == "redis://localhost:6379/0"
    assert pool.kwargs["max_connections"] == 100
    assert pool.kwargs["retry_on_timeout"] is True
    assert pool.kwargs["socket_connect_timeout"] == 5
    assert pool.kwargs["socket_timeout"] == 5
    assert pool.kwargs["health_check_interval"] == 30


def test_pool_is_cached_per_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool_module, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(pool_module, "_redis_module", FakeRedisModule)

    first = get_redis_pool("redis://localhost:6379/0")
    second = get_redis_pool("redis://localhost:6379/0")

    assert first is second


def test_different_urls_create_different_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool_module, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(pool_module, "_redis_module", FakeRedisModule)

    first = get_redis_pool("redis://first:6379/0")
    second = get_redis_pool("redis://second:6379/0")

    assert first is not second
    assert first.url == "redis://first:6379/0"
    assert second.url == "redis://second:6379/0"
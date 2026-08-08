"""
Contract tests for Redis health checks.
"""

from __future__ import annotations

from nova.redis.health import (
    RedisHealthReport,
    check_async_redis_health,
    check_redis_health,
)


class FakeSyncPingClient:
    def ping(self) -> bool:
        return True


class FakeSyncFailingClient:
    def ping(self) -> bool:
        raise RuntimeError("sync ping failed")


class FakeAsyncPingClient:
    async def ping(self) -> bool:
        return True


class FakeAsyncFailingClient:
    async def ping(self) -> bool:
        raise RuntimeError("async ping failed")


def test_sync_health_ok() -> None:
    report = check_redis_health(client=FakeSyncPingClient())

    assert isinstance(report, RedisHealthReport)
    assert report.is_healthy is True
    assert report.error is None
    assert report.latency_ms >= 0


def test_sync_health_failure() -> None:
    report = check_redis_health(client=FakeSyncFailingClient())

    assert report.is_healthy is False
    assert report.latency_ms == -1.0
    assert report.error is not None
    assert "sync ping failed" in report.error


async def test_async_health_ok() -> None:
    report = await check_async_redis_health(client=FakeAsyncPingClient())

    assert isinstance(report, RedisHealthReport)
    assert report.is_healthy is True
    assert report.error is None
    assert report.latency_ms >= 0


async def test_async_health_failure() -> None:
    report = await check_async_redis_health(client=FakeAsyncFailingClient())

    assert report.is_healthy is False
    assert report.latency_ms == -1.0
    assert report.error is not None
    assert "async ping failed" in report.error

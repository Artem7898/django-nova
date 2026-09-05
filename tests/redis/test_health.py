"""Tests for Redis health checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova.redis.health import (
    RedisHealthReport,
    check_async_redis_health,
    check_redis_health,
)


class TestRedisHealthReport:
    """Tests for the RedisHealthReport value object."""

    def test_report_contains_expected_values(self) -> None:
        report = RedisHealthReport(
            is_healthy=True,
            latency_ms=1.234,
        )

        assert report.is_healthy is True
        assert report.latency_ms == 1.234
        assert report.error is None

    def test_report_contains_error(self) -> None:
        report = RedisHealthReport(
            is_healthy=False,
            latency_ms=-1.0,
            error="Connection refused",
        )

        assert report.is_healthy is False
        assert report.latency_ms == -1.0
        assert report.error == "Connection refused"

    def test_report_is_immutable(self) -> None:
        report = RedisHealthReport(
            is_healthy=True,
            latency_ms=1.0,
        )

        with pytest.raises(AttributeError):
            report.is_healthy = False  # type: ignore[misc]


class TestCheckRedisHealth:
    """Tests for synchronous Redis health checks."""

    def test_returns_healthy_report_when_ping_succeeds(self) -> None:
        client = MagicMock()
        client.ping.return_value = True

        report = check_redis_health(client)

        assert report.is_healthy is True
        assert report.error is None
        assert report.latency_ms >= 0
        client.ping.assert_called_once_with()

    def test_returns_unhealthy_report_for_unexpected_ping_response(
        self,
    ) -> None:
        client = MagicMock()
        client.ping.return_value = False

        report = check_redis_health(client)

        assert report.is_healthy is False
        assert report.error == "Unexpected PING response"
        assert report.latency_ms >= 0
        client.ping.assert_called_once_with()

    def test_returns_unhealthy_report_when_ping_raises(self) -> None:
        client = MagicMock()
        client.ping.side_effect = ConnectionError("Redis unavailable")

        report = check_redis_health(client)

        assert report.is_healthy is False
        assert report.latency_ms == -1.0
        assert report.error == "Redis unavailable"
        client.ping.assert_called_once_with()

    def test_uses_injected_client_without_global_factory(self) -> None:
        client = MagicMock()
        client.ping.return_value = True

        with patch("nova.redis.client.get_redis_client") as factory:
            report = check_redis_health(client)

        assert report.is_healthy is True
        factory.assert_not_called()
        client.ping.assert_called_once_with()

    def test_uses_global_client_when_client_is_not_provided(self) -> None:
        client = MagicMock()
        client.ping.return_value = True

        with patch(
            "nova.redis.client.get_redis_client",
            return_value=client,
        ) as factory:
            report = check_redis_health()

        assert report.is_healthy is True
        assert report.error is None
        factory.assert_called_once_with()
        client.ping.assert_called_once_with()


class TestCheckAsyncRedisHealth:
    """Tests for asynchronous Redis health checks."""

    @pytest.mark.asyncio
    async def test_returns_healthy_report_when_ping_succeeds(self) -> None:
        client = MagicMock()
        client.ping = AsyncMock(return_value=True)

        report = await check_async_redis_health(client)

        assert report.is_healthy is True
        assert report.error is None
        assert report.latency_ms >= 0
        client.ping.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_returns_unhealthy_report_for_unexpected_ping_response(
        self,
    ) -> None:
        client = MagicMock()
        client.ping = AsyncMock(return_value=False)

        report = await check_async_redis_health(client)

        assert report.is_healthy is False
        assert report.error == "Unexpected PING response"
        assert report.latency_ms >= 0
        client.ping.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_returns_unhealthy_report_when_ping_raises(self) -> None:
        client = MagicMock()
        client.ping = AsyncMock(
            side_effect=ConnectionError("Async Redis unavailable"),
        )

        report = await check_async_redis_health(client)

        assert report.is_healthy is False
        assert report.latency_ms == -1.0
        assert report.error == "Async Redis unavailable"
        client.ping.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_uses_injected_client_without_global_factory(self) -> None:
        client = MagicMock()
        client.ping = AsyncMock(return_value=True)

        with patch("nova.redis.client.get_async_redis_client") as factory:
            report = await check_async_redis_health(client)

        assert report.is_healthy is True
        factory.assert_not_called()
        client.ping.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_uses_global_client_when_client_is_not_provided(self) -> None:
        client = MagicMock()
        client.ping = AsyncMock(return_value=True)

        with patch(
            "nova.redis.client.get_async_redis_client",
            return_value=client,
        ) as factory:
            report = await check_async_redis_health()

        assert report.is_healthy is True
        assert report.error is None
        factory.assert_called_once_with()
        client.ping.assert_awaited_once_with()

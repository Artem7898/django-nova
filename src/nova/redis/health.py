"""
Redis Health Checks.
Returns structured data suitable for Django's check framework or Prometheus metrics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RedisHealthReport:
    is_healthy: bool
    latency_ms: float
    error: str | None = None


def check_redis_health() -> RedisHealthReport:
    """
    Synchronously pings Redis and measures latency.
    """
    try:
        from nova.redis.client import get_redis_client
        client = get_redis_client()

        start = time.perf_counter()
        response = client.ping()
        latency = (time.perf_counter() - start) * 1000  # to ms

        if response is True:
            return RedisHealthReport(is_healthy=True, latency_ms=round(latency, 3))
        else:
            return RedisHealthReport(is_healthy=False, latency_ms=latency, error="Unexpected PING response")

    except Exception as e:
        return RedisHealthReport(is_healthy=False, latency_ms=-1.0, error=str(e))


async def check_async_redis_health() -> RedisHealthReport:
    """
    Asynchronously pings Redis and measures latency.
    """
    try:
        from nova.redis.client import get_async_redis_client
        client = get_async_redis_client()

        start = time.perf_counter()
        response = await client.ping()
        latency = (time.perf_counter() - start) * 1000

        if response is True:
            return RedisHealthReport(is_healthy=True, latency_ms=round(latency, 3))
        else:
            return RedisHealthReport(is_healthy=False, latency_ms=latency, error="Unexpected PING response")

    except Exception as e:
        return RedisHealthReport(is_healthy=False, latency_ms=-1.0, error=str(e))
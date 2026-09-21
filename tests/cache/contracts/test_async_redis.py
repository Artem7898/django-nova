"""
Contract tests for async Redis cache backend.

These tests run against fakeredis, so no real Redis server is required.
"""

from __future__ import annotations

import pytest

from nova.cache.backends.redis_backend import AsyncRedisCacheBackend
from tests.cache.contracts import AsyncCacheBackendContract

pytest.importorskip("redis", reason="redis is required for Redis cache contracts")

fakeredis = pytest.importorskip(
    "fakeredis",
    reason="fakeredis is required for Redis cache contracts",
)

try:
    FakeAsyncRedis = fakeredis.FakeAsyncRedis
except AttributeError:
    FakeAsyncRedis = fakeredis.aioredis.FakeRedis


class TestAsyncRedisCacheBackendContract(AsyncCacheBackendContract):
    def create_backend(self) -> AsyncRedisCacheBackend:
        return AsyncRedisCacheBackend(
            client=FakeAsyncRedis(decode_responses=False),
            key_prefix="nova-test-async",
        )

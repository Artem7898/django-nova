"""
Contract tests for Redis cache backend.

These tests run against fakeredis, so no real Redis server is required.
"""

from __future__ import annotations

import pytest
from tests.cache.contracts import CacheBackendContract

from nova.cache.backends.redis import RedisCacheBackend

pytest.importorskip("redis", reason="redis is required for Redis cache contracts")

fakeredis = pytest.importorskip(
    "fakeredis",
    reason="fakeredis is required for Redis cache contracts",
)


class TestRedisCacheBackendContract(CacheBackendContract):
    def create_backend(self) -> RedisCacheBackend:
        return RedisCacheBackend(
            client=fakeredis.FakeRedis(decode_responses=False),
            key_prefix="nova-test",
        )
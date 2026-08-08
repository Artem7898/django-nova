"""
Contract tests for distributed rate limiter.
"""

from __future__ import annotations

from typing import Any

import pytest

from nova.core.exceptions import NovaRateLimitError
from nova.redis.rate_limiter import async_check_rate_limit, check_rate_limit


class FakeSyncRateClient:
    def __init__(self, results: list[int]) -> None:
        self.results = list(results)
        self.calls: list[tuple[int, tuple[Any, ...]]] = []

    def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.calls.append((numkeys, args))
        return self.results.pop(0)


class FakeAsyncRateClient:
    def __init__(self, results: list[int]) -> None:
        self.results = list(results)
        self.calls: list[tuple[int, tuple[Any, ...]]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.calls.append((numkeys, args))
        return self.results.pop(0)


def test_sync_rate_limit_allowed() -> None:
    client = FakeSyncRateClient([1])

    assert check_rate_limit("api:user", 5, 60, client=client) is True

    numkeys, args = client.calls[0]

    assert numkeys == 1
    assert args[0] == "nova:rl:api:user"
    assert args[1] == 60
    assert args[2] == 5


def test_sync_rate_limit_rejected() -> None:
    client = FakeSyncRateClient([0])

    with pytest.raises(NovaRateLimitError):
        check_rate_limit("api:user", 5, 60, client=client)


async def test_async_rate_limit_allowed() -> None:
    client = FakeAsyncRateClient([1])

    assert await async_check_rate_limit("api:user", 5, 60, client=client) is True

    numkeys, args = client.calls[0]

    assert numkeys == 1
    assert args[0] == "nova:rl:api:user"
    assert args[1] == 60
    assert args[2] == 5


async def test_async_rate_limit_rejected() -> None:
    client = FakeAsyncRateClient([0])

    with pytest.raises(NovaRateLimitError):
        await async_check_rate_limit("api:user", 5, 60, client=client)
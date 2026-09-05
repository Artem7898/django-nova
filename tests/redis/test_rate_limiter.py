"""Contract tests for the distributed Redis rate limiter."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest

from nova.core.exceptions import NovaRateLimitError
from nova.redis.rate_limiter import (
    _SLIDING_WINDOW_LUA,
    async_check_rate_limit,
    check_rate_limit,
)


class FakeSyncRateClient:
    """Minimal synchronous Redis client used by contract tests."""

    def __init__(
        self,
        results: list[int] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.calls: list[tuple[str, int, tuple[Any, ...]]] = []

    def eval(
        self,
        script: str,
        numkeys: int,
        *args: Any,
    ) -> int:
        """Record EVAL call and return the configured Redis result."""
        self.calls.append((script, numkeys, args))

        if self.error is not None:
            raise self.error

        return self.results.pop(0)


class FakeAsyncRateClient:
    """Minimal asynchronous Redis client used by contract tests."""

    def __init__(
        self,
        results: list[int] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.calls: list[tuple[str, int, tuple[Any, ...]]] = []

    async def eval(
        self,
        script: str,
        numkeys: int,
        *args: Any,
    ) -> int:
        """Record EVAL call and return the configured Redis result."""
        self.calls.append((script, numkeys, args))

        if self.error is not None:
            raise self.error

        return self.results.pop(0)


# ---------------------------------------------------------------------------
# Lua contract
# ---------------------------------------------------------------------------


def test_sliding_window_lua_is_atomic_redis_script() -> None:
    """The rate limiter must use a Redis Lua script for atomic decisions."""
    assert isinstance(_SLIDING_WINDOW_LUA, str)
    assert _SLIDING_WINDOW_LUA.strip()

    assert "ZREMRANGEBYSCORE" in _SLIDING_WINDOW_LUA
    assert "ZCARD" in _SLIDING_WINDOW_LUA
    assert "ZADD" in _SLIDING_WINDOW_LUA
    assert "EXPIRE" in _SLIDING_WINDOW_LUA
    assert "count < limit" in _SLIDING_WINDOW_LUA


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------


def test_sync_rate_limit_allowed() -> None:
    """Redis allow result must produce True."""
    client = FakeSyncRateClient([1])

    result = check_rate_limit(
        "api:user",
        5,
        60,
        client=client,
    )

    assert result is True
    assert len(client.calls) == 1

    script, numkeys, args = client.calls[0]

    assert script == _SLIDING_WINDOW_LUA
    assert numkeys == 1
    assert args[0] == "nova:rl:api:user"
    assert args[1] == 60
    assert args[2] == 5
    assert isinstance(args[3], float)
    assert isinstance(args[4], str)


def test_sync_rate_limit_rejected() -> None:
    """Redis reject result must raise NovaRateLimitError."""
    client = FakeSyncRateClient([0])

    with pytest.raises(
        NovaRateLimitError,
        match="Rate limit exceeded for 'api:user'",
    ):
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )


def test_sync_rate_limit_preserves_argument_order() -> None:
    """Redis EVAL arguments must follow the Lua contract."""
    client = FakeSyncRateClient([1])

    check_rate_limit(
        "orders",
        100,
        30,
        client=client,
    )

    _, numkeys, args = client.calls[0]

    assert numkeys == 1
    assert args[0] == "nova:rl:orders"
    assert args[1] == 30
    assert args[2] == 100
    assert isinstance(args[3], float)
    assert isinstance(args[4], str)


def test_sync_rate_limit_uses_uuid_request_id() -> None:
    """Every request must receive its UUID as the Lua request member."""
    client = FakeSyncRateClient([1, 1])

    first_uuid = uuid.UUID(
        "00000000-0000-0000-0000-000000000001",
    )
    second_uuid = uuid.UUID(
        "00000000-0000-0000-0000-000000000002",
    )

    with patch(
        "nova.redis.rate_limiter.uuid.uuid4",
        side_effect=[first_uuid, second_uuid],
    ):
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    first_request_id = client.calls[0][2][4]
    second_request_id = client.calls[1][2][4]

    assert first_request_id == str(first_uuid)
    assert second_request_id == str(second_uuid)
    assert first_request_id != second_request_id


def test_sync_rate_limit_uses_current_timestamp() -> None:
    """Current time must be passed to the Lua script."""
    client = FakeSyncRateClient([1])

    with patch(
        "nova.redis.rate_limiter.time.time",
        return_value=1234.567,
    ):
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    args = client.calls[0][2]

    assert args[3] == 1234.567


# ---------------------------------------------------------------------------
# Sync client resolution
# ---------------------------------------------------------------------------


def test_sync_rate_limit_uses_injected_client() -> None:
    """Explicitly supplied client must be used directly."""
    client = FakeSyncRateClient([1])

    with patch(
        "nova.redis.client.get_redis_client",
        side_effect=AssertionError(
            "global Redis client must not be used",
        ),
    ):
        result = check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    assert result is True
    assert len(client.calls) == 1


def test_sync_rate_limit_lazily_resolves_client() -> None:
    """Redis client must be resolved lazily when not injected."""
    client = FakeSyncRateClient([1])

    with patch(
        "nova.redis.client.get_redis_client",
        return_value=client,
    ) as get_client:
        result = check_rate_limit(
            "api:user",
            5,
            60,
        )

    assert result is True
    get_client.assert_called_once()
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Sync errors
# ---------------------------------------------------------------------------


def test_sync_rate_limit_error_contains_key() -> None:
    """Rate-limit error must identify the logical key."""
    client = FakeSyncRateClient([0])

    with pytest.raises(
        NovaRateLimitError,
        match="api:user",
    ):
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )


def test_sync_rate_limit_error_is_nova_rate_limit_error() -> None:
    """Rejected requests must expose the documented exception type."""
    client = FakeSyncRateClient([0])

    with pytest.raises(
        NovaRateLimitError,
        match=r"Rate limit exceeded for 'api:user'",
    ) as exc_info:
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    assert "limit=5" in str(exc_info.value)
    assert "window_secs=60" in str(exc_info.value)


def test_sync_rate_limit_propagates_redis_error() -> None:
    """Unexpected Redis errors must not be swallowed."""
    redis_error = RuntimeError("Redis unavailable")
    client = FakeSyncRateClient(error=redis_error)

    with pytest.raises(
        RuntimeError,
        match="Redis unavailable",
    ):
        check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )


# ---------------------------------------------------------------------------
# Sync key contract
# ---------------------------------------------------------------------------


def test_sync_rate_limit_namespaces_keys() -> None:
    """Logical keys must use the Nova rate-limit namespace."""
    client = FakeSyncRateClient([1, 1])

    check_rate_limit(
        "user:1",
        5,
        60,
        client=client,
    )
    check_rate_limit(
        "user:2",
        5,
        60,
        client=client,
    )

    first_key = client.calls[0][2][0]
    second_key = client.calls[1][2][0]

    assert first_key == "nova:rl:user:1"
    assert second_key == "nova:rl:user:2"
    assert first_key != second_key


def test_sync_rate_limit_forwards_zero_limit() -> None:
    """limit=0 must be forwarded unchanged to Redis."""
    client = FakeSyncRateClient([0])

    with pytest.raises(NovaRateLimitError):
        check_rate_limit(
            "api:user",
            0,
            60,
            client=client,
        )

    args = client.calls[0][2]

    assert args[1] == 60
    assert args[2] == 0


def test_sync_rate_limit_forwards_zero_window() -> None:
    """window_secs=0 must be forwarded unchanged to Redis."""
    client = FakeSyncRateClient([1])

    check_rate_limit(
        "api:user",
        5,
        0,
        client=client,
    )

    args = client.calls[0][2]

    assert args[1] == 0
    assert args[2] == 5


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_rate_limit_allowed() -> None:
    """Redis allow result must produce True."""
    client = FakeAsyncRateClient([1])

    result = await async_check_rate_limit(
        "api:user",
        5,
        60,
        client=client,
    )

    assert result is True
    assert len(client.calls) == 1

    script, numkeys, args = client.calls[0]

    assert script == _SLIDING_WINDOW_LUA
    assert numkeys == 1
    assert args[0] == "nova:rl:api:user"
    assert args[1] == 60
    assert args[2] == 5
    assert isinstance(args[3], float)
    assert isinstance(args[4], str)


@pytest.mark.asyncio
async def test_async_rate_limit_rejected() -> None:
    """Redis reject result must raise NovaRateLimitError."""
    client = FakeAsyncRateClient([0])

    with pytest.raises(
        NovaRateLimitError,
        match="Rate limit exceeded for 'api:user'",
    ):
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )


@pytest.mark.asyncio
async def test_async_rate_limit_preserves_argument_order() -> None:
    """Redis EVAL arguments must follow the Lua contract."""
    client = FakeAsyncRateClient([1])

    await async_check_rate_limit(
        "orders",
        100,
        30,
        client=client,
    )

    _, numkeys, args = client.calls[0]

    assert numkeys == 1
    assert args[0] == "nova:rl:orders"
    assert args[1] == 30
    assert args[2] == 100
    assert isinstance(args[3], float)
    assert isinstance(args[4], str)


@pytest.mark.asyncio
async def test_async_rate_limit_uses_uuid_request_id() -> None:
    """Every async request must receive its UUID as the Lua member."""
    client = FakeAsyncRateClient([1, 1])

    first_uuid = uuid.UUID(
        "00000000-0000-0000-0000-000000000001",
    )
    second_uuid = uuid.UUID(
        "00000000-0000-0000-0000-000000000002",
    )

    with patch(
        "nova.redis.rate_limiter.uuid.uuid4",
        side_effect=[first_uuid, second_uuid],
    ):
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    first_request_id = client.calls[0][2][4]
    second_request_id = client.calls[1][2][4]

    assert first_request_id == str(first_uuid)
    assert second_request_id == str(second_uuid)
    assert first_request_id != second_request_id


@pytest.mark.asyncio
async def test_async_rate_limit_uses_current_timestamp() -> None:
    """Current time must be passed to the Lua script."""
    client = FakeAsyncRateClient([1])

    with patch(
        "nova.redis.rate_limiter.time.time",
        return_value=9876.543,
    ):
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    args = client.calls[0][2]

    assert args[3] == 9876.543


# ---------------------------------------------------------------------------
# Async client resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_rate_limit_uses_injected_client() -> None:
    """Explicitly supplied client must be used directly."""
    client = FakeAsyncRateClient([1])

    with patch(
        "nova.redis.client.get_async_redis_client",
        side_effect=AssertionError(
            "global Redis client must not be used",
        ),
    ):
        result = await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    assert result is True
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_async_rate_limit_lazily_resolves_client() -> None:
    """Redis client must be resolved lazily when not injected."""
    client = FakeAsyncRateClient([1])

    with patch(
        "nova.redis.client.get_async_redis_client",
        return_value=client,
    ) as get_client:
        result = await async_check_rate_limit(
            "api:user",
            5,
            60,
        )

    assert result is True
    get_client.assert_called_once()
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Async errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_rate_limit_error_contains_key() -> None:
    """Rate-limit error must identify the logical key."""
    client = FakeAsyncRateClient([0])

    with pytest.raises(
        NovaRateLimitError,
        match="api:user",
    ):
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )


@pytest.mark.asyncio
async def test_async_rate_limit_error_is_nova_rate_limit_error() -> None:
    """Rejected requests must expose the documented exception type."""
    client = FakeAsyncRateClient([0])

    with pytest.raises(
        NovaRateLimitError,
        match=r"Rate limit exceeded for 'api:user'",
    ) as exc_info:
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )

    assert "limit=5" in str(exc_info.value)
    assert "window_secs=60" in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_rate_limit_propagates_redis_error() -> None:
    """Unexpected Redis errors must not be swallowed."""
    redis_error = RuntimeError("Redis unavailable")
    client = FakeAsyncRateClient(error=redis_error)

    with pytest.raises(
        RuntimeError,
        match="Redis unavailable",
    ):
        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=client,
        )


# ---------------------------------------------------------------------------
# Async key contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_rate_limit_namespaces_keys() -> None:
    """Logical keys must use the Nova rate-limit namespace."""
    client = FakeAsyncRateClient([1, 1])

    await async_check_rate_limit(
        "user:1",
        5,
        60,
        client=client,
    )
    await async_check_rate_limit(
        "user:2",
        5,
        60,
        client=client,
    )

    first_key = client.calls[0][2][0]
    second_key = client.calls[1][2][0]

    assert first_key == "nova:rl:user:1"
    assert second_key == "nova:rl:user:2"
    assert first_key != second_key


@pytest.mark.asyncio
async def test_async_rate_limit_forwards_zero_limit() -> None:
    """limit=0 must be forwarded unchanged to Redis."""
    client = FakeAsyncRateClient([0])

    with pytest.raises(NovaRateLimitError):
        await async_check_rate_limit(
            "api:user",
            0,
            60,
            client=client,
        )

    args = client.calls[0][2]

    assert args[1] == 60
    assert args[2] == 0


@pytest.mark.asyncio
async def test_async_rate_limit_forwards_zero_window() -> None:
    """window_secs=0 must be forwarded unchanged to Redis."""
    client = FakeAsyncRateClient([1])

    await async_check_rate_limit(
        "api:user",
        5,
        0,
        client=client,
    )

    args = client.calls[0][2]

    assert args[1] == 0
    assert args[2] == 5


# ---------------------------------------------------------------------------
# Sync / async parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_and_async_build_same_redis_arguments() -> None:
    """Sync and async APIs must build equivalent Redis requests."""
    sync_client = FakeSyncRateClient([1])
    async_client = FakeAsyncRateClient([1])

    request_uuid = uuid.UUID(
        "00000000-0000-0000-0000-000000000123",
    )

    with (
        patch(
            "nova.redis.rate_limiter.time.time",
            return_value=12345.0,
        ),
        patch(
            "nova.redis.rate_limiter.uuid.uuid4",
            return_value=request_uuid,
        ),
    ):
        check_rate_limit(
            "api:user",
            5,
            60,
            client=sync_client,
        )

        await async_check_rate_limit(
            "api:user",
            5,
            60,
            client=async_client,
        )

    sync_script, sync_numkeys, sync_args = sync_client.calls[0]
    async_script, async_numkeys, async_args = async_client.calls[0]

    assert sync_script == async_script == _SLIDING_WINDOW_LUA
    assert sync_numkeys == async_numkeys == 1
    assert sync_args == async_args


@pytest.mark.asyncio
async def test_async_rate_limit_accepts_nested_logical_key() -> None:
    """Separators inside logical keys must remain intact."""
    client = FakeAsyncRateClient([1])

    await async_check_rate_limit(
        "tenant:user:123",
        10,
        120,
        client=client,
    )

    assert client.calls[0][2][0] == "nova:rl:tenant:user:123"

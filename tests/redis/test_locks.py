"""Tests for Redis distributed locks."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova.core.exceptions import NovaCacheError
from nova.redis.locks import (
    UNLOCK_SCRIPT,
    AsyncDistributedLock,
    async_lock,
)


class FakeRedisLockClient:
    """
    Minimal in-memory Redis-like client for lock semantics.

    It models only the Redis operations required by AsyncDistributedLock:
    SET NX PX and the ownership-aware Lua unlock operation.
    """

    def __init__(self) -> None:
        self._value: str | None = None
        self._expires_at: float | None = None

    def _is_expired(self) -> bool:
        if self._expires_at is None:
            return False

        if time.monotonic() >= self._expires_at:
            self._value = None
            self._expires_at = None
            return True

        return False

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool,
        px: int,
    ) -> bool:
        assert nx is True
        assert name.startswith("nova:lock:")
        assert px >= 0

        self._is_expired()

        if self._value is not None:
            return False

        self._value = value
        self._expires_at = time.monotonic() + (px / 1000)

        return True

    async def eval(
        self,
        script: str,
        numkeys: int,
        name: str,
        identifier: str,
    ) -> int:
        assert script == UNLOCK_SCRIPT
        assert numkeys == 1
        assert name.startswith("nova:lock:")

        self._is_expired()

        if self._value == identifier:
            self._value = None
            self._expires_at = None
            return 1

        return 0

    def current_owner(self) -> str | None:
        self._is_expired()
        return self._value


class TestAsyncDistributedLock:
    """Tests for AsyncDistributedLock."""

    @pytest.mark.asyncio
    async def test_acquire_success(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)

        lock = AsyncDistributedLock(
            "resource",
            client=client,
        )

        result = await lock.acquire()

        assert result is True
        client.set.assert_awaited_once_with(
            "nova:lock:resource",
            lock.identifier,
            nx=True,
            px=10_000,
        )

    @pytest.mark.asyncio
    async def test_lock_name_is_prefixed(self) -> None:
        client = MagicMock()

        lock = AsyncDistributedLock(
            "orders:123",
            client=client,
        )

        assert lock.name == "nova:lock:orders:123"

    @pytest.mark.asyncio
    async def test_custom_timeout_is_converted_to_milliseconds(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)

        lock = AsyncDistributedLock(
            "resource",
            timeout=2.5,
            client=client,
        )

        await lock.acquire()

        client.set.assert_awaited_once_with(
            "nova:lock:resource",
            lock.identifier,
            nx=True,
            px=2500,
        )

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_blocking_timeout_expires(
        self,
    ) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=False)

        lock = AsyncDistributedLock(
            "resource",
            blocking_timeout=0.0,
            client=client,
        )

        result = await lock.acquire()

        assert result is False
        assert client.set.await_count == 1

    @pytest.mark.asyncio
    async def test_acquire_retries_until_lock_is_available(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(
            side_effect=[False, False, True],
        )

        lock = AsyncDistributedLock(
            "resource",
            blocking_timeout=1.0,
            client=client,
        )

        with patch(
            "nova.redis.locks.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
            result = await lock.acquire()

        assert result is True
        assert client.set.await_count == 3
        assert sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_release_does_nothing_when_lock_not_acquired(self) -> None:
        client = MagicMock()
        client.eval = AsyncMock()

        lock = AsyncDistributedLock(
            "resource",
            client=client,
        )

        await lock.release()

        client.eval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_uses_atomic_unlock_script(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)
        client.eval = AsyncMock(return_value=1)

        lock = AsyncDistributedLock(
            "resource",
            client=client,
        )

        await lock.acquire()
        await lock.release()

        client.eval.assert_awaited_once_with(
            UNLOCK_SCRIPT,
            1,
            "nova:lock:resource",
            lock.identifier,
        )

    @pytest.mark.asyncio
    async def test_release_marks_lock_as_not_acquired(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)
        client.eval = AsyncMock(return_value=1)

        lock = AsyncDistributedLock(
            "resource",
            client=client,
        )

        await lock.acquire()
        await lock.release()

        # A second release must be a no-op.
        await lock.release()

        assert client.eval.await_count == 1

    @pytest.mark.asyncio
    async def test_context_manager_acquires_and_releases(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)
        client.eval = AsyncMock(return_value=1)

        async with AsyncDistributedLock(
            "resource",
            client=client,
        ) as lock:
            assert lock.name == "nova:lock:resource"
            assert client.set.await_count == 1

        assert client.eval.await_count == 1

    @pytest.mark.asyncio
    async def test_context_manager_raises_when_lock_cannot_be_acquired(
        self,
    ) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=False)

        with pytest.raises(NovaCacheError, match="Could not acquire lock"):
            async with AsyncDistributedLock(
                "resource",
                blocking_timeout=0.0,
                client=client,
            ):
                pytest.fail("Context body must not execute")

    @pytest.mark.asyncio
    async def test_context_manager_releases_when_body_raises(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)
        client.eval = AsyncMock(return_value=1)

        with pytest.raises(RuntimeError, match="boom"):
            async with AsyncDistributedLock(
                "resource",
                client=client,
            ):
                raise RuntimeError("boom")

        client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lazy_client_resolution(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)

        with patch(
            "nova.redis.client.get_async_redis_client",
            return_value=client,
        ) as factory:
            lock = AsyncDistributedLock("resource")

            result = await lock.acquire()

        assert result is True
        factory.assert_called_once_with()
        client.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_injected_client_bypasses_global_factory(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)

        with patch(
            "nova.redis.client.get_async_redis_client",
        ) as factory:
            lock = AsyncDistributedLock(
                "resource",
                client=client,
            )

            result = await lock.acquire()

        assert result is True
        factory.assert_not_called()
        client.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_lock_shortcut_acquires_and_releases(self) -> None:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)
        client.eval = AsyncMock(return_value=1)

        async with async_lock(
            "resource",
            client=client,
        ):
            assert client.set.await_count == 1

        assert client.eval.await_count == 1

    def test_each_lock_has_unique_identifier(self) -> None:
        first = AsyncDistributedLock("resource")
        second = AsyncDistributedLock("resource")

        assert first.identifier != second.identifier

    def test_unlock_script_checks_owner_before_delete(self) -> None:
        assert "get" in UNLOCK_SCRIPT
        assert "ARGV[1]" in UNLOCK_SCRIPT
        assert "del" in UNLOCK_SCRIPT

    @pytest.mark.asyncio
    async def test_mutual_exclusion_only_one_lock_can_acquire_resource(
        self,
    ) -> None:
        """
        Two lock instances targeting the same resource cannot own it
        simultaneously.
        """
        client = FakeRedisLockClient()

        first = AsyncDistributedLock(
            "resource",
            blocking_timeout=0.0,
            client=client,
        )
        second = AsyncDistributedLock(
            "resource",
            blocking_timeout=0.0,
            client=client,
        )

        first_result, second_result = await asyncio.gather(
            first.acquire(),
            second.acquire(),
        )

        assert first_result is True
        assert second_result is False

        assert client.current_owner() == first.identifier

        await first.release()

    @pytest.mark.asyncio
    async def test_ownership_isolation_non_owner_cannot_release_current_lock(
        self,
    ) -> None:
        """
        A lock owner cannot be replaced by a different identifier during
        release. The ownership check is performed atomically by Redis.
        """
        client = FakeRedisLockClient()

        owner = AsyncDistributedLock(
            "resource",
            client=client,
        )

        other = AsyncDistributedLock(
            "resource",
            client=client,
        )

        assert await owner.acquire() is True

        # The second lock cannot acquire the resource while owner holds it.
        assert await other.acquire() is False

        # Simulate a stale owner attempting an ownership-aware release.
        result = await client.eval(
            UNLOCK_SCRIPT,
            1,
            owner.name,
            other.identifier,
        )

        assert result == 0
        assert client.current_owner() == owner.identifier

        await owner.release()

    @pytest.mark.asyncio
    async def test_expired_lock_cannot_delete_new_owner_lock(self) -> None:
        """
        When the original lock expires and another owner acquires the same
        resource, the stale owner must not be able to delete the new lock.
        """
        client = FakeRedisLockClient()

        stale_owner = AsyncDistributedLock(
            "resource",
            timeout=0.01,
            blocking_timeout=0.0,
            client=client,
        )

        new_owner = AsyncDistributedLock(
            "resource",
            timeout=10.0,
            blocking_timeout=0.0,
            client=client,
        )

        assert await stale_owner.acquire() is True
        assert client.current_owner() == stale_owner.identifier

        # Wait for the original Redis TTL to expire.
        await asyncio.sleep(0.02)

        assert await new_owner.acquire() is True
        assert client.current_owner() == new_owner.identifier

        # The stale owner still believes it acquired the lock locally.
        assert stale_owner._acquired is True

        # Releasing the stale owner must NOT delete the new owner's lock.
        await stale_owner.release()

        assert client.current_owner() == new_owner.identifier

        # The new owner can still release its own lock.
        await new_owner.release()

        assert client.current_owner() is None

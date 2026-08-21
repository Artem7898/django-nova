"""
Contract tests for distributed locks.
"""

from __future__ import annotations

from typing import Any

from nova.redis.locks import AsyncDistributedLock, async_lock


class FakeAsyncLockClient:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def set(
        self,
        key: str,
        value: Any,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        if nx and key in self.store:
            return False

        self.store[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        key = args[0]
        identifier = args[1]

        if self.store.get(key) == identifier:
            del self.store[key]
            return 1

        return 0


async def test_lock_acquire_release() -> None:
    client = FakeAsyncLockClient()
    lock = AsyncDistributedLock("resource", client=client)

    assert await lock.acquire() is True
    assert lock._acquired is True
    assert "nova:lock:resource" in client.store

    await lock.release()

    assert lock._acquired is False
    assert "nova:lock:resource" not in client.store


async def test_lock_prevents_second_acquisition() -> None:
    client = FakeAsyncLockClient()

    first = AsyncDistributedLock(
        "resource",
        blocking_timeout=0,
        client=client,
    )
    second = AsyncDistributedLock(
        "resource",
        blocking_timeout=0,
        client=client,
    )

    assert await first.acquire() is True
    assert await second.acquire() is False

    await first.release()


async def test_lock_context_manager() -> None:
    client = FakeAsyncLockClient()

    async with AsyncDistributedLock("resource", client=client):
        assert "nova:lock:resource" in client.store

    assert "nova:lock:resource" not in client.store


async def test_async_lock_helper() -> None:
    client = FakeAsyncLockClient()

    async with async_lock("resource", client=client):
        assert "nova:lock:resource" in client.store

    assert "nova:lock:resource" not in client.store

"""
Distributed Locks implementation based on Redis.
Provides strict typing and safety guarantees for concurrent operations.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from nova.core.exceptions import NovaCacheError

# Lua script ensures atomicity: only the owner can release the lock
UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class AsyncDistributedLock:
    """
    An async context manager for distributed locking using Redis.
    Usage:
        async with AsyncDistributedLock("my_resource", timeout=10.0):
            # do work
    """

    def __init__(self, name: str, timeout: float = 10.0, blocking_timeout: float = 5.0) -> None:
        self.name = f"nova:lock:{name}"
        self.timeout = int(timeout * 1000)  # Redis uses milliseconds for PX
        self.blocking_timeout = blocking_timeout
        self.identifier: str = str(uuid.uuid4())
        self._acquired: bool = False

    async def __aenter__(self) -> AsyncDistributedLock:
        await self.acquire()
        if not self._acquired:
            # If we couldn't acquire within blocking_timeout, we must fail safely
            raise NovaCacheError(f"Could not acquire lock '{self.name}' within {self.blocking_timeout}s")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.release()

    async def acquire(self) -> bool:
        from nova.redis.client import get_async_redis_client

        client = get_async_redis_client()
        # SET key value NX PX timeout
        self._acquired = bool(
            await client.set(
                self.name,
                self.identifier,
                nx=True,
                px=self.timeout
            )
        )
        return self._acquired

    async def release(self) -> None:
        if not self._acquired:
            return

        from nova.redis.client import get_async_redis_client
        client = get_async_redis_client()

        await client.eval(UNLOCK_SCRIPT, 1, self.name, self.identifier)
        self._acquired = False


# Convenience function for quick locking without instantiating the class
@asynccontextmanager
async def async_lock(resource_name: str, timeout: float = 10.0) -> AsyncIterator[None]:
    """Async context manager shortcut for distributed locks."""
    lock = AsyncDistributedLock(resource_name, timeout=timeout)
    await lock.acquire()
    if not lock._acquired:
        raise NovaCacheError(f"Failed to acquire async lock for '{resource_name}'")
    try:
        yield
    finally:
        await lock.release()
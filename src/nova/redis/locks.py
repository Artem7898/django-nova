"""
Distributed Locks implementation based on Redis.

Provides strict typing and safety guarantees for concurrent operations.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from nova.core.exceptions import NovaCacheError

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
    """

    def __init__(
        self,
        name: str,
        timeout: float = 10.0,
        blocking_timeout: float = 5.0,
        *,
        client: Any | None = None,
    ) -> None:
        self.name = f"nova:lock:{name}"
        self.timeout = int(timeout * 1000)
        self.blocking_timeout = blocking_timeout
        self.identifier: str = str(uuid.uuid4())
        self._acquired: bool = False
        self._client = client

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        from nova.redis.client import get_async_redis_client

        return get_async_redis_client()

    async def __aenter__(self) -> AsyncDistributedLock:
        await self.acquire()

        if not self._acquired:
            raise NovaCacheError(
                f"Could not acquire lock '{self.name}' within {self.blocking_timeout}s"
            )

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.release()

    async def acquire(self) -> bool:
        client = await self._get_client()

        deadline = time.monotonic() + self.blocking_timeout

        while True:
            self._acquired = bool(
                await client.set(
                    self.name,
                    self.identifier,
                    nx=True,
                    px=self.timeout,
                )
            )

            if self._acquired:
                return True

            if time.monotonic() >= deadline:
                return False

            await asyncio.sleep(0.01)

    async def release(self) -> None:
        if not self._acquired:
            return

        client = await self._get_client()

        await client.eval(UNLOCK_SCRIPT, 1, self.name, self.identifier)

        self._acquired = False


@asynccontextmanager
async def async_lock(
    resource_name: str,
    timeout: float = 10.0,
    *,
    client: Any | None = None,
) -> AsyncGenerator[None, None]:
    """
    Async context manager shortcut for distributed locks.
    """
    async with AsyncDistributedLock(
        resource_name,
        timeout=timeout,
        client=client,
    ):
        yield
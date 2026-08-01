"""Task Backend Implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AsyncioBackend",
    "RedisBackend",
    "TaskBackend",
]

def __getattr__(name: str):
    if name == "TaskBackend":
        from nova.tasks.backends.protocol import TaskBackend
        return TaskBackend
    # Aliases for backward compatibility if someone used the old names
    if name == "AsyncioTaskBackend":
        from nova.tasks.backends.asyncio_backend import AsyncioBackend
        return AsyncioBackend
    if name == "AsyncioBackend":
        from nova.tasks.backends.asyncio_backend import AsyncioBackend
        return AsyncioBackend
    if name == "RedisTaskBackend":
        from nova.tasks.backends.redis_backend import RedisBackend
        return RedisBackend
    if name == "RedisBackend":
        from nova.tasks.backends.redis_backend import RedisBackend
        return RedisBackend

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.tasks.backends.asyncio_backend import AsyncioBackend
    from nova.tasks.backends.protocol import TaskBackend
    from nova.tasks.backends.redis_backend import RedisBackend
"""Task Backend Implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AsyncioTaskBackend",
    "RedisTaskBackend",
    "TaskBackend",
]

def __getattr__(name: str):
    if name == "TaskBackend":
        from nova.tasks.backends.protocol import TaskBackend
        return TaskBackend
    if name == "AsyncioTaskBackend":
        from nova.tasks.backends.asyncio_backend import AsyncioTaskBackend
        return AsyncioTaskBackend
    if name == "RedisTaskBackend":
        from nova.tasks.backends.redis_backend import RedisTaskBackend
        return RedisTaskBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.tasks.backends.asyncio_backend import AsyncioTaskBackend
    from nova.tasks.backends.protocol import TaskBackend
    from nova.tasks.backends.redis_backend import RedisTaskBackend
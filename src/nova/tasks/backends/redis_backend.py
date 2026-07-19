"""Redis Task Backend (Stub for future implementation)."""

from __future__ import annotations

from typing import Any

from nova.tasks.exceptions import TaskBackendError


class RedisBackend:
    """Placeholder for Redis-based task queue."""

    def __init__(self, url: str = "redis://localhost:6379/0", **kwargs: Any) -> None:
        raise TaskBackendError("Redis backend is not yet implemented. Please use 'asyncio' backend.")
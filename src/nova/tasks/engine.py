"""
Thin Task Engine Facade.
"""

from __future__ import annotations

from typing import Any

from nova.tasks.backends.asyncio_backend import AsyncioBackend
from nova.tasks.models import TaskResult


class NovaTaskEngine:
    """Orchestrator for background tasks."""

    def __init__(self, backend: Any = None, **kwargs: Any) -> None:
        if backend is not None:
            self._backend = backend
        else:
            self._backend = AsyncioBackend(**kwargs)

    async def start(self) -> None:
        await self._backend.start()

    async def stop(self) -> None:
        await self._backend.stop()


    def submit(
        self,
        func: Any,
        *args: Any,
        delay: float = 0.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        **kwargs: Any
    ) -> str:
        return self._backend.submit(
            func, *args, delay=delay, max_retries=max_retries, retry_delay=retry_delay, **kwargs
        )

    def get_status(self, task_id: str) -> TaskResult | None:
        return self._backend.get_status(task_id)


_engine: NovaTaskEngine | None = None

def get_engine() -> NovaTaskEngine:
    global _engine
    if _engine is None:
        _engine = NovaTaskEngine()
    return _engine
"""Protocol defining the contract for Task Backends."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

from nova.tasks.models import TaskResult

TaskFunc = Callable[..., Coroutine[Any, Any, Any]]


@runtime_checkable
class TaskBackend(Protocol):
    """Contract for any task execution backend (Asyncio, Redis, Celery)."""

    def submit(
        self,
        func: TaskFunc,
        *args: Any,
        delay: float = 0.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        **kwargs: Any
    ) -> str:
        """Submit a task and return its ID."""
        ...

    def get_status(self, task_id: str) -> TaskResult | None:
        """Return the current state of the task."""
        ...

    def get_result(self, task_id: str) -> Any:
        """Return the raw result or raise an error."""
        ...

    async def start(self) -> None:
        """Start consuming/processing tasks (if applicable)."""
        ...

    async def stop(self) -> None:
        """Gracefully stop the backend."""
        ...
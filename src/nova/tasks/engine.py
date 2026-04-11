"""
Built-in asyncio task engine.
Scientific context: Eliminates Celery/RabbitMQ dependency for compute-bound
research tasks (simulations, ML inference) running inside a single container.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TaskFunc = Callable[..., Coroutine[Any, Any, Any]]


class TaskResult(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "PENDING"  # PENDING, RUNNING, SUCCESS, FAILED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None


class NovaTaskEngine:
    """
    In-process async task runner.
    Uses asyncio.Queue for task distribution.
    """

    def __init__(self, max_concurrent: int = 4) -> None:
        self._queue: asyncio.Queue[tuple[str, TaskFunc, tuple, dict]] = asyncio.Queue()
        self._results: dict[str, TaskResult] = {}
        self._max_concurrent = max_concurrent
        self._workers: list[asyncio.Task] = []

    async def _worker(self) -> None:
        while True:
            task_id, func, args, kwargs = await self._queue.get()
            self._results[task_id].status = "RUNNING"
            self._results[task_id].started_at = datetime.now(UTC)

            try:
                res = await func(*args, **kwargs)
                self._results[task_id].status = "SUCCESS"
                self._results[task_id].result = res
            except Exception as e:
                self._results[task_id].status = "FAILED"
                self._results[task_id].error = str(e)
                logger.exception("Task %s failed", task_id)
            finally:
                self._results[task_id].finished_at = datetime.now(UTC)
                self._queue.task_done()

    async def start(self) -> None:
        for _ in range(self._max_concurrent):
            self._workers.append(asyncio.create_task(self._worker()))
        logger.info("Nova Task Engine started with %d workers", self._max_concurrent)

    async def stop(self) -> None:
        await self._queue.join()
        for w in self._workers:
            w.cancel()

    def submit(self, func: TaskFunc, *args: Any, **kwargs: Any) -> str:
        task_id = uuid.uuid4().hex
        self._results[task_id] = TaskResult(id=task_id)
        self._queue.put_nowait((task_id, func, args, kwargs))
        return task_id

    def get_status(self, task_id: str) -> TaskResult | None:
        return self._results.get(task_id)


# Singleton
_engine: NovaTaskEngine | None = None


def get_engine() -> NovaTaskEngine:
    global _engine
    if _engine is None:
        _engine = NovaTaskEngine()
    return _engine
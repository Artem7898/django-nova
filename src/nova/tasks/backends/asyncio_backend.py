"""In-process Asyncio Task Backend with Retries and Delays."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nova.core.tracing import nova_span
from nova.tasks.models import TaskResult

logger = logging.getLogger(__name__)

TaskFunc = Any


@dataclass
class _TaskPayload:
    """Internal structure to pass task metadata through the queue."""
    task_id: str
    func: TaskFunc
    args: tuple
    kwargs: dict
    delay: float = 0.0
    max_retries: int = 0
    retry_delay: float = 1.0
    attempts: int = 0


class AsyncioBackend:
    """Executes tasks using a local asyncio queue, supporting retries and delays."""

    def __init__(self, max_concurrent: int = 4) -> None:

        self._queue: asyncio.Queue[_TaskPayload] = asyncio.Queue()
        self._results: dict[str, TaskResult] = {}
        self._max_concurrent = max_concurrent
        self._workers: list[asyncio.Task] = []

    async def _worker(self) -> None:
        while True:
            payload: _TaskPayload = await self._queue.get()
            result = self._results[payload.task_id]


            if payload.attempts == 0 and payload.delay > 0:
                await asyncio.sleep(payload.delay)

            result.status = "RUNNING"
            if result.started_at is None:
                result.started_at = datetime.now(UTC)

            with nova_span("nova.task.execute", task_id=payload.task_id, task_name=payload.func.__name__) as span:
                start_exec = time.perf_counter()
                try:
                    res = await payload.func(*payload.args, **payload.kwargs)
                    result.status = "SUCCESS"
                    result.result = res
                    result.attempts = payload.attempts + 1
                    if span:
                        span.set_attribute("task.status", "SUCCESS")
                except Exception as e:
                    # 2. Обработка повторных попыток (Retries)
                    if payload.attempts < payload.max_retries:
                        payload.attempts += 1
                        result.attempts = payload.attempts
                        result.status = "RETRYING"
                        result.error = f"Attempt {payload.attempts} failed: {e!s}"

                        if span:
                            span.set_attribute("task.status", "RETRYING")
                            span.set_attribute("task.attempt", payload.attempts)

                        logger.warning("Task %s failed, retrying %d/%d", payload.task_id, payload.attempts, payload.max_retries)


                        await asyncio.sleep(payload.retry_delay)
                        self._queue.put_nowait(payload)
                    else:
                        # All attempts have been exhausted
                        result.status = "FAILED"
                        result.error = str(e)
                        result.attempts = payload.attempts + 1
                        if span:
                            span.set_attribute("task.status", "FAILED")
                            span.set_attribute("task.attempts", payload.attempts + 1)
                        logger.exception("Task %s failed permanently after %d attempts", payload.task_id, payload.max_retries + 1)
                finally:
                    exec_time = (time.perf_counter() - start_exec) * 1000
                    if span:
                        span.set_attribute("task.execution_time_ms", exec_time)

                    if result.status not in ("RETRYING",):
                        result.finished_at = datetime.now(UTC)

                    self._queue.task_done()

    async def start(self) -> None:
        for _ in range(self._max_concurrent):
            self._workers.append(asyncio.create_task(self._worker()))
        logger.info("Asyncio Backend started with %d workers", self._max_concurrent)

    async def stop(self) -> None:
        await self._queue.join()
        for w in self._workers:
            w.cancel()

    def submit(
        self,
        func: TaskFunc,
        *args: Any,
        delay: float = 0.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        **kwargs: Any
    ) -> str:
        task_id = uuid.uuid4().hex

        with nova_span("nova.task.submit", task_name=func.__name__) as span:
            self._results[task_id] = TaskResult(id=task_id)

            payload = _TaskPayload(
                task_id=task_id,
                func=func,
                args=args,
                kwargs=kwargs,
                delay=delay,
                max_retries=max_retries,
                retry_delay=retry_delay
            )

            self._queue.put_nowait(payload)
            if span:
                span.set_attribute("task.id", task_id)
                if delay > 0:
                    span.set_attribute("task.delay_seconds", delay)
                if max_retries > 0:
                    span.set_attribute("task.max_retries", max_retries)

        return task_id

    def get_status(self, task_id: str) -> TaskResult | None:
        return self._results.get(task_id)

    def get_result(self, task_id: str) -> Any:
        result = self._results.get(task_id)
        if result and result.status == "SUCCESS":
            return result.result
        return None
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
from nova.tasks.backends.protocol import TaskFunc
from nova.tasks.models import TaskResult

logger = logging.getLogger(__name__)


@dataclass
class _TaskPayload:
    """Internal structure to pass task metadata through the queue."""

    task_id: str
    func: TaskFunc
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    delay: float = 0.0
    max_retries: int = 0
    retry_delay: float = 1.0
    attempts: int = 0


class AsyncioBackend:
    """Executes tasks using a local asyncio queue, supporting retries and delays."""

    def __init__(self, max_concurrent: int = 4) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be greater than 0")

        self._queue = asyncio.Queue()
        self._results = {}
        self._max_concurrent = max_concurrent
        self._workers = []
        self._started = False

    async def _worker(self) -> None:
        while True:
            payload: _TaskPayload = await self._queue.get()
            result = self._results[payload.task_id]

            if payload.attempts == 0 and payload.delay > 0:
                await asyncio.sleep(payload.delay)

            result.status = "RUNNING"
            if result.started_at is None:
                result.started_at = datetime.now(UTC)

            task_name = getattr(payload.func, "__name__", "unknown")

            with nova_span(
                "nova.task.execute", task_id=payload.task_id, task_name=task_name
            ) as span:
                start_exec = time.perf_counter()
                try:
                    res = await payload.func(*payload.args, **payload.kwargs)
                    result.status = "SUCCESS"
                    result.result = res
                    result.attempts = payload.attempts + 1
                    if span:
                        span.set_attribute("task.status", "SUCCESS")
                except Exception as e:
                    if payload.attempts < payload.max_retries:
                        payload.attempts += 1
                        result.attempts = payload.attempts
                        result.status = "RETRYING"
                        result.error = f"Attempt {payload.attempts} failed: {e!s}"

                        if span:
                            span.set_attribute("task.status", "RETRYING")
                            span.set_attribute("task.attempt", payload.attempts)

                        logger.warning(
                            "Task %s failed, retrying %d/%d",
                            payload.task_id,
                            payload.attempts,
                            payload.max_retries,
                        )

                        await asyncio.sleep(payload.retry_delay)
                        self._queue.put_nowait(payload)
                    else:
                        result.status = "FAILED"
                        result.error = str(e)
                        result.attempts = payload.attempts + 1
                        if span:
                            span.set_attribute("task.status", "FAILED")
                            span.set_attribute("task.attempts", payload.attempts + 1)
                        logger.exception(
                            "Task %s failed permanently after %d attempts",
                            payload.task_id,
                            payload.max_retries + 1,
                        )
                finally:
                    exec_time = (time.perf_counter() - start_exec) * 1000
                    if span:
                        span.set_attribute("task.execution_time_ms", exec_time)

                    if result.status not in ("RETRYING",):
                        result.finished_at = datetime.now(UTC)

                    self._queue.task_done()

    async def start(self) -> None:
        if self._started:
            return

        self._workers = [asyncio.create_task(self._worker()) for _ in range(self._max_concurrent)]
        self._started = True

    async def stop(self) -> None:
        if not self._workers:
            return

        await self._queue.join()

        workers = self._workers
        self._workers = []

        for worker in workers:
            worker.cancel()

        await asyncio.gather(*workers, return_exceptions=True)

    def submit(
        self,
        func: TaskFunc,
        *args: Any,
        delay: float = 0.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        **kwargs: Any,
    ) -> str:
        task_id = uuid.uuid4().hex

        task_name = getattr(func, "__name__", "unknown")

        with nova_span("nova.task.submit", task_name=task_name) as span:
            self._results[task_id] = TaskResult(id=task_id)

            payload = _TaskPayload(
                task_id=task_id,
                func=func,
                args=args,
                kwargs=kwargs,
                delay=delay,
                max_retries=max_retries,
                retry_delay=retry_delay,
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

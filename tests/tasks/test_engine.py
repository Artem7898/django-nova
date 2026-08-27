from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from nova.tasks.backends.protocol import TaskBackend, TaskFunc
from nova.tasks.engine import NovaTaskEngine
from nova.tasks.models import TaskResult


class StubTaskBackend:
    """Deterministic backend double for testing NovaTaskEngine."""

    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.submit_calls: list[
            tuple[
                TaskFunc,
                tuple[object, ...],
                float,
                int,
                float,
                dict[str, object],
            ]
        ] = []
        self.status_calls: list[str] = []

        self.task_id = "task-123"
        self.status_result = TaskResult(
            id=self.task_id,
            status="PENDING",
        )

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    def submit(
        self,
        func: TaskFunc,
        *args: object,
        delay: float = 0.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        **kwargs: object,
    ) -> str:
        self.submit_calls.append(
            (
                func,
                args,
                delay,
                max_retries,
                retry_delay,
                kwargs,
            )
        )
        return self.task_id

    def get_status(self, task_id: str) -> TaskResult | None:
        self.status_calls.append(task_id)

        if task_id == self.task_id:
            return self.status_result

        return None

    def get_result(self, task_id: str) -> Any:
        if task_id == self.task_id:
            return self.status_result.result

        return None


def test_engine_accepts_injected_backend() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    task_id = engine.submit(sample_task, 42, priority="high")

    assert task_id == "task-123"
    assert len(backend.submit_calls) == 1


@pytest.mark.asyncio
async def test_start_delegates_to_backend() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    await engine.start()

    assert backend.start_calls == 1


@pytest.mark.asyncio
async def test_stop_delegates_to_backend() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    await engine.stop()

    assert backend.stop_calls == 1


@pytest.mark.asyncio
async def test_start_and_stop_are_forwarded_without_extra_behavior() -> None:
    """The engine remains a thin lifecycle facade."""

    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    await engine.start()
    await engine.stop()

    assert backend.start_calls == 1
    assert backend.stop_calls == 1


async def sample_task(user_id: int, *, priority: str) -> str:
    return f"{user_id}:{priority}"


def test_submit_delegates_to_backend() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    task_id = engine.submit(
        sample_task,
        42,
        delay=2.5,
        max_retries=3,
        retry_delay=0.5,
        priority="high",
    )

    assert task_id == "task-123"
    assert len(backend.submit_calls) == 1

    (
        func,
        args,
        delay,
        max_retries,
        retry_delay,
        kwargs,
    ) = backend.submit_calls[0]

    assert func is sample_task
    assert args == (42,)
    assert delay == 2.5
    assert max_retries == 3
    assert retry_delay == 0.5
    assert kwargs == {"priority": "high"}


def test_submit_preserves_default_execution_options() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    task_id = engine.submit(sample_task, 7, priority="normal")

    assert task_id == "task-123"

    (
        func,
        args,
        delay,
        max_retries,
        retry_delay,
        kwargs,
    ) = backend.submit_calls[0]

    assert func is sample_task
    assert args == (7,)
    assert delay == 0.0
    assert max_retries == 0
    assert retry_delay == 1.0
    assert kwargs == {"priority": "normal"}


def test_get_status_delegates_to_backend() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    result = engine.get_status("task-123")

    assert result is backend.status_result
    assert backend.status_calls == ["task-123"]


def test_get_status_returns_none_for_unknown_task() -> None:
    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    result = engine.get_status("missing-task")

    assert result is None
    assert backend.status_calls == ["missing-task"]


def test_engine_does_not_execute_task_directly() -> None:
    """
    Task execution belongs to the backend.

    The engine must only forward the callable and execution parameters.
    """

    backend = StubTaskBackend()
    engine = NovaTaskEngine(backend=backend)

    executed = False

    async def task() -> None:
        nonlocal executed
        executed = True

    task_id = engine.submit(task)

    assert task_id == "task-123"
    assert executed is False
    assert len(backend.submit_calls) == 1


def test_engine_preserves_backend_status_identity() -> None:
    """The facade must not rebuild or transform TaskResult objects."""

    backend = StubTaskBackend()
    backend.status_result = TaskResult(
        id="task-123",
        status="SUCCESS",
        started_at=datetime.now(UTC),
        result={"value": 42},
        attempts=1,
    )

    engine = NovaTaskEngine(backend=backend)

    result = engine.get_status("task-123")

    assert result is backend.status_result
    assert result is not None
    assert result.status == "SUCCESS"
    assert result.result == {"value": 42}
    assert result.attempts == 1


def test_engine_accepts_any_task_backend_implementation() -> None:
    """
    Structural typing contract:

    NovaTaskEngine should work with a backend that implements the
    TaskBackend protocol without inheriting from a Nova base class.
    """

    backend = StubTaskBackend()

    assert isinstance(backend, TaskBackend)

    engine = NovaTaskEngine(backend=backend)

    assert engine._backend is backend


def test_engine_submission_errors_are_not_swallowed() -> None:
    class FailingBackend(StubTaskBackend):
        def submit(
            self,
            func: TaskFunc,
            *args: object,
            delay: float = 0.0,
            max_retries: int = 0,
            retry_delay: float = 1.0,
            **kwargs: object,
        ) -> str:
            raise RuntimeError("backend unavailable")

    engine = NovaTaskEngine(backend=FailingBackend())

    with pytest.raises(RuntimeError, match="backend unavailable"):
        engine.submit(sample_task, 1, priority="high")

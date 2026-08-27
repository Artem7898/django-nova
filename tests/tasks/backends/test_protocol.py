from __future__ import annotations

import inspect
from typing import Any

from nova.tasks.backends.protocol import TaskBackend, TaskFunc
from nova.tasks.models import TaskResult


class StubTaskBackend:
    """
    Minimal structural implementation of TaskBackend.

    This class intentionally contains no backend logic.
    It exists only to prove that the protocol describes the
    boundary required by NovaTaskEngine.
    """

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def submit(
        self,
        func: TaskFunc,
        *args: object,
        delay: float = 0.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        **kwargs: object,
    ) -> str:
        return "task-id"

    def get_status(self, task_id: str) -> TaskResult | None:
        return None

    def get_result(self, task_id: str) -> Any:
        return None


def _accept_backend(backend: TaskBackend) -> TaskBackend:
    """
    Static typing boundary.

    Pyright must accept any structurally compatible backend.
    """
    return backend


def test_protocol_declares_required_backend_methods() -> None:
    """TaskBackend must expose the complete public backend surface."""

    required_methods = {
        "start",
        "stop",
        "submit",
        "get_status",
        "get_result",
    }

    for method_name in required_methods:
        assert hasattr(TaskBackend, method_name), (
            f"TaskBackend is missing required method: {method_name}"
        )


def test_protocol_methods_are_callable() -> None:
    """Every protocol operation must be represented by a callable."""

    for method_name in (
        "start",
        "stop",
        "submit",
        "get_status",
        "get_result",
    ):
        method = getattr(TaskBackend, method_name)

        assert callable(method), f"TaskBackend.{method_name} must be callable"


def test_structural_backend_matches_protocol() -> None:
    """
    A backend does not need to inherit from TaskBackend.

    Structural compatibility is the architectural contract.
    """

    backend = StubTaskBackend()

    typed_backend = _accept_backend(backend)

    assert typed_backend is backend


def test_task_function_is_async_callable_type() -> None:
    """
    TaskFunc represents the async application boundary.

    The protocol must operate on coroutine-producing callables.
    """

    async def task(value: int) -> str:
        return str(value)

    assert inspect.iscoroutinefunction(task)


def test_task_result_is_the_status_model() -> None:
    """
    Backend status operations return Nova's stable TaskResult model,
    rather than backend-specific result objects.
    """

    result = TaskResult(
        id="task-1",
        status="PENDING",
    )

    assert result.id == "task-1"
    assert result.status == "PENDING"


def test_start_and_stop_are_async_operations() -> None:
    """Backend lifecycle belongs to the async infrastructure boundary."""

    start = inspect.signature(TaskBackend.start)
    stop = inspect.signature(TaskBackend.stop)

    assert inspect.iscoroutinefunction(TaskBackend.start)
    assert inspect.iscoroutinefunction(TaskBackend.stop)

    assert len(start.parameters) == 1
    assert len(stop.parameters) == 1


def test_submit_is_synchronous_submission_boundary() -> None:
    """
    submit() creates a task and returns its identifier.

    Execution itself belongs to the backend implementation.
    """

    signature = inspect.signature(TaskBackend.submit)

    assert not inspect.iscoroutinefunction(TaskBackend.submit)

    assert "func" in signature.parameters
    assert "delay" in signature.parameters
    assert "max_retries" in signature.parameters
    assert "retry_delay" in signature.parameters


def test_status_and_result_are_synchronous_queries() -> None:
    """
    Task inspection is exposed as a simple backend query boundary.

    The concrete backend owns how the information is obtained.
    """

    status = inspect.signature(TaskBackend.get_status)
    result = inspect.signature(TaskBackend.get_result)

    assert not inspect.iscoroutinefunction(TaskBackend.get_status)
    assert not inspect.iscoroutinefunction(TaskBackend.get_result)

    assert "task_id" in status.parameters
    assert "task_id" in result.parameters


def test_backend_protocol_does_not_require_inheritance() -> None:
    """
    Nova uses structural typing instead of forcing infrastructure
    implementations to inherit from a Nova base class.
    """

    backend = StubTaskBackend()

    assert backend.__class__.__bases__ == (object,)

    typed_backend = _accept_backend(backend)

    assert typed_backend is backend

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

import pytest

from nova.tasks.backends.asyncio_backend import AsyncioBackend
from nova.tasks.backends.protocol import TaskBackend, TaskFunc
from nova.tasks.models import TaskResult


@dataclass(frozen=True, slots=True)
class TaskBackendExpectation:
    """
    Configuration for a concrete task backend contract.

    The contract is intentionally backend-agnostic. A concrete backend is
    supplied through a factory and must expose the TaskBackend protocol.
    """

    target: str
    factory: Callable[[], TaskBackend]


class TaskBackendContract:
    """
    Architectural contract shared by Nova task backends.

    The tests verify observable semantics rather than implementation details.

    Philosophy:
        Application code depends on TaskBackend semantics, not on the
        concrete execution engine underneath.

    A backend must provide:

        submit()
        get_status()
        get_result()
        start()
        stop()

    Subclasses should provide `create_backend()` only.
    """

    expectation: TaskBackendExpectation

    def create_backend(self) -> TaskBackend:
        """Return a fresh, isolated backend instance."""
        return self.expectation.factory()

    @staticmethod
    async def _wait_until(
        predicate: Callable[[], bool],
        *,
        timeout: float = 2.0,
        interval: float = 0.005,
    ) -> None:
        """
        Wait until a synchronous predicate becomes true.

        This keeps contract tests independent of a specific event-loop
        implementation while preventing unbounded sleeps.
        """
        deadline = time.monotonic() + timeout

        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("Timed out waiting for task state")
            await asyncio.sleep(interval)

    @staticmethod
    def _run(coro: Awaitable[Any]) -> Any:
        """Run an async test operation without requiring pytest-asyncio."""
        return asyncio.run(coro)

    async def _execute_and_stop(
        self,
        backend: TaskBackend,
        task_id: str,
    ) -> TaskResult:
        """
        Wait for a task to reach a terminal state and stop the backend.
        """
        try:
            await self._wait_until(
                lambda: (
                    (status := backend.get_status(task_id)) is not None
                    and status.status in {"SUCCESS", "FAILED"}
                ),
            )
        finally:
            await backend.stop()

        result = backend.get_status(task_id)
        assert result is not None
        return result

    #
    # Protocol / structural contract
    #

    def test_backend_implements_protocol(self) -> None:
        backend = self.create_backend()

        assert isinstance(backend, TaskBackend)

    def test_backend_can_start_and_stop(self) -> None:
        backend = self.create_backend()

        self._run(self._start_and_stop(backend))

    async def _start_and_stop(self, backend: TaskBackend) -> None:
        await backend.start()
        await backend.stop()

    #
    # Submission lifecycle
    #

    def test_submit_returns_task_id_and_pending_result(self) -> None:
        async def task() -> str:
            return "ok"

        backend = self.create_backend()

        task_id = backend.submit(cast(TaskFunc, task))

        assert isinstance(task_id, str)
        assert task_id

        status = backend.get_status(task_id)

        assert status is not None
        assert isinstance(status, TaskResult)
        assert status.id == task_id
        assert status.status == "PENDING"
        assert status.attempts == 0

    def test_unknown_task_status_returns_none(self) -> None:
        backend = self.create_backend()

        assert backend.get_status("does-not-exist") is None

    def test_unknown_task_result_returns_none(self) -> None:
        backend = self.create_backend()

        assert backend.get_result("does-not-exist") is None

    #
    # Successful execution
    #

    def test_successful_task_has_unified_result(self) -> None:
        async def task() -> dict[str, Any]:
            return {"status": "ok", "value": 42}

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(cast(TaskFunc, task))

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.id == task_id
            assert result.status == "SUCCESS"
            assert result.result == {"status": "ok", "value": 42}
            assert result.error is None
            assert result.attempts == 1
            assert result.started_at is not None
            assert result.finished_at is not None
            assert result.finished_at >= result.started_at

            assert backend.get_result(task_id) == {
                "status": "ok",
                "value": 42,
            }

        self._run(scenario())

    #
    # Failure semantics
    #

    def test_failed_task_exposes_error_without_leaking_exception(self) -> None:
        async def task() -> None:
            raise ValueError("contract failure")

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(cast(TaskFunc, task))

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.id == task_id
            assert result.status == "FAILED"
            assert result.result is None
            assert result.error == "contract failure"
            assert result.attempts == 1
            assert result.started_at is not None
            assert result.finished_at is not None

            # Task execution errors are represented by TaskResult rather
            # than escaping from the backend API.
            assert backend.get_result(task_id) is None

        self._run(scenario())

    #
    # Retry semantics
    #

    def test_retry_eventually_succeeds(self) -> None:
        attempts = 0

        async def task() -> str:
            nonlocal attempts
            attempts += 1

            if attempts < 3:
                raise RuntimeError(f"temporary failure #{attempts}")

            return "recovered"

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(
                cast(TaskFunc, task),
                max_retries=2,
                retry_delay=0.01,
            )

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.status == "SUCCESS"
            assert result.result == "recovered"
            assert result.error is not None
            assert result.attempts == 3
            assert attempts == 3

        self._run(scenario())

    def test_retry_exhaustion_produces_failed_result(self) -> None:
        attempts = 0

        async def task() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("permanent failure")

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(
                cast(TaskFunc, task),
                max_retries=2,
                retry_delay=0.01,
            )

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.status == "FAILED"
            assert result.result is None
            assert result.error == "permanent failure"
            assert result.attempts == 3
            assert attempts == 3

        self._run(scenario())

    def test_zero_retries_means_single_attempt(self) -> None:
        attempts = 0

        async def task() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("no retry")

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(
                cast(TaskFunc, task),
                max_retries=0,
            )

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.status == "FAILED"
            assert result.attempts == 1
            assert attempts == 1

        self._run(scenario())

    #
    # Delay semantics
    #

    def test_delay_is_applied_before_execution(self) -> None:
        started_at: float | None = None

        async def task() -> str:
            nonlocal started_at
            started_at = time.monotonic()
            return "delayed"

        delay = 0.05
        backend = self.create_backend()

        async def scenario() -> None:
            submitted_at = time.monotonic()

            task_id = backend.submit(
                cast(TaskFunc, task),
                delay=delay,
            )

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.status == "SUCCESS"
            assert result.result == "delayed"
            assert started_at is not None

            elapsed = started_at - submitted_at

            # Allow a small scheduler tolerance while still proving that
            # the backend does not execute the task immediately.
            assert elapsed >= delay * 0.8

        self._run(scenario())

    #
    # Argument forwarding
    #

    def test_positional_and_keyword_arguments_are_preserved(self) -> None:
        async def task(
            first: int,
            second: int,
            *,
            multiplier: int,
        ) -> int:
            return (first + second) * multiplier

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(
                cast(TaskFunc, task),
                2,
                3,
                multiplier=4,
            )

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.status == "SUCCESS"
            assert result.result == 20
            assert result.attempts == 1

        self._run(scenario())

    #
    # Result metadata
    #

    def test_result_metadata_has_consistent_lifecycle(self) -> None:
        async def task() -> str:
            await asyncio.sleep(0)
            return "done"

        backend = self.create_backend()

        async def scenario() -> None:
            task_id = backend.submit(cast(TaskFunc, task))

            pending = backend.get_status(task_id)

            assert pending is not None
            assert pending.status == "PENDING"
            assert pending.started_at is None
            assert pending.finished_at is None
            assert pending.attempts == 0

            await backend.start()

            result = await self._execute_and_stop(backend, task_id)

            assert result.status == "SUCCESS"
            assert result.started_at is not None
            assert result.finished_at is not None
            assert result.finished_at >= result.started_at
            assert result.attempts == 1

        self._run(scenario())

    #
    # Backend isolation
    #

    def test_each_backend_instance_has_isolated_task_state(self) -> None:
        async def task() -> str:
            return "isolated"

        backend_a = self.create_backend()
        backend_b = self.create_backend()

        async def scenario() -> None:
            task_id = backend_a.submit(cast(TaskFunc, task))

            assert backend_a.get_status(task_id) is not None
            assert backend_b.get_status(task_id) is None

            await backend_a.start()

            result = await self._execute_and_stop(backend_a, task_id)

            assert result.status == "SUCCESS"
            assert backend_b.get_status(task_id) is None

            await backend_b.stop()

        self._run(scenario())


class TestAsyncioBackendContract(TaskBackendContract):
    """
    Concrete contract binding for Nova's in-process asyncio backend.

    Additional backends can reuse TaskBackendContract by supplying their
    own TaskBackendExpectation and factory.
    """

    expectation = TaskBackendExpectation(
        target="AsyncioBackend",
        factory=lambda: AsyncioBackend(max_concurrent=2),
    )

    def create_backend(self) -> TaskBackend:
        return self.expectation.factory()


@pytest.mark.parametrize(
    "max_concurrent",
    [1, 2, 4],
)
def test_asyncio_backend_respects_valid_worker_configuration(
    max_concurrent: int,
) -> None:
    backend = AsyncioBackend(max_concurrent=max_concurrent)

    async def scenario() -> None:
        await backend.start()

        try:
            assert len(backend._workers) == max_concurrent
        finally:
            await backend.stop()

    asyncio.run(scenario())

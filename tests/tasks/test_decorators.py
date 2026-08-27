from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import pytest

from nova.tasks import decorators

TaskFunc = Callable[..., Coroutine[Any, Any, Any]]


class StubTaskEngine:
    """Minimal engine double for testing the decorator boundary."""

    def __init__(self, task_id: str = "task-123") -> None:
        self.task_id = task_id
        self.calls: list[tuple[TaskFunc, tuple[object, ...], dict[str, object]]] = []

    def submit(
        self,
        func: TaskFunc,
        *args: object,
        **kwargs: object,
    ) -> str:
        self.calls.append((func, args, kwargs))
        return self.task_id


@pytest.mark.asyncio
async def test_nova_task_submits_original_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decorator must submit the original callable, not the wrapper."""

    engine = StubTaskEngine()
    original_called = False

    async def process(value: int) -> int:
        nonlocal original_called
        original_called = True
        return value * 2

    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    decorated = decorators.nova_task()(process)

    result = await decorated(21)

    assert result == "task-123"
    assert original_called is False

    assert len(engine.calls) == 1
    func, args, kwargs = engine.calls[0]

    assert func is process
    assert args == (21,)
    assert kwargs == {}


@pytest.mark.asyncio
async def test_nova_task_uses_engine_at_invocation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Engine resolution happens when the decorated task is invoked.

    This keeps task registration independent from engine construction.
    """

    engine = StubTaskEngine()

    async def process(value: int) -> int:
        return value

    decorated = decorators.nova_task()(process)

    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    result = await decorated(10)

    assert result == "task-123"
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_nova_task_forwards_args_and_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positional and keyword arguments must cross the task boundary unchanged."""

    engine = StubTaskEngine()
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    async def process(user_id: int, *, priority: str) -> None:
        return None

    decorated = decorators.nova_task()(process)

    result = await decorated(42, priority="high")

    assert result == "task-123"
    assert len(engine.calls) == 1

    func, args, kwargs = engine.calls[0]

    assert func is process
    assert args == (42,)
    assert kwargs == {"priority": "high"}


@pytest.mark.asyncio
async def test_nova_task_preserves_function_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """functools.wraps must preserve the public identity of the task."""

    engine = StubTaskEngine()
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    async def process(value: int) -> int:
        """Process a value."""
        return value

    decorated = decorators.nova_task()(process)

    await decorated(1)

    assert decorated.__name__ == "process"
    assert decorated.__doc__ == "Process a value."


@pytest.mark.asyncio
async def test_nova_task_default_name_uses_function_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an explicit name, Nova must use the original function name."""

    engine = StubTaskEngine()
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    async def process_order(order_id: int) -> None:
        return None

    decorated = decorators.nova_task()(process_order)

    await decorated(100)

    assert decorated._nova_task_name == "process_order"
    assert decorated._is_nova_task is True


@pytest.mark.asyncio
async def test_nova_task_explicit_name_overrides_function_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit Nova task name must override the Python function name."""

    engine = StubTaskEngine()
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    async def process_order(order_id: int) -> None:
        return None

    decorated = decorators.nova_task("orders.process")(process_order)

    await decorated(100)

    assert decorated._nova_task_name == "orders.process"
    assert decorated._is_nova_task is True


@pytest.mark.asyncio
async def test_nova_task_returns_engine_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decorator must expose the backend-generated task identifier."""

    engine = StubTaskEngine(task_id="nova-task-42")
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    async def process() -> None:
        return None

    decorated = decorators.nova_task()(process)

    result = await decorated()

    assert result == "nova-task-42"


@pytest.mark.asyncio
async def test_nova_task_does_not_execute_business_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Decorating a task must enqueue it rather than execute business logic.

    Actual execution belongs to the configured TaskBackend.
    """

    engine = StubTaskEngine()
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    state = {"executed": False}

    async def process() -> None:
        state["executed"] = True

    decorated = decorators.nova_task()(process)

    await decorated()

    assert state["executed"] is False
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_nova_task_propagates_engine_submission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Infrastructure submission errors must not be silently swallowed."""

    class FailingEngine(StubTaskEngine):
        def submit(
            self,
            func: TaskFunc,
            *args: object,
            **kwargs: object,
        ) -> str:
            raise RuntimeError("submission failed")

    engine = FailingEngine()
    monkeypatch.setattr(decorators, "get_engine", lambda: engine)

    async def process() -> None:
        return None

    decorated = decorators.nova_task()(process)

    with pytest.raises(RuntimeError, match="submission failed"):
        await decorated()

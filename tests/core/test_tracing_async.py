"""Regression tests for tracing coroutine execution."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from nova.core import tracing


@pytest.mark.parametrize(
    "factory",
    [tracing.trace_task, tracing.trace_model, tracing.trace_cache, tracing.trace_validation],
    ids=["task", "model", "cache", "validation"],
)
def test_span_covers_await(factory) -> None:
    events: list[str] = []

    @contextmanager
    def span_context(*args, **kwargs) -> Generator[None, None, None]:
        events.append("enter")
        try:
            yield None
        finally:
            events.append("exit")

    async def operation(value: int, *, increment: int) -> int:
        """Coroutine metadata must survive decoration."""
        events.append("before")
        await asyncio.sleep(0)
        events.append("after")
        return value + increment

    with (
        patch.object(tracing, "OTEL_AVAILABLE", True),
        patch.object(tracing, "nova_span", span_context),
    ):
        wrapped = factory()(operation)
        assert inspect.iscoroutinefunction(wrapped)
        assert wrapped.__name__ == operation.__name__
        assert wrapped.__doc__ == operation.__doc__
        assert inspect.signature(wrapped) == inspect.signature(operation)
        assert asyncio.run(wrapped(40, increment=2)) == 42

    assert events == ["enter", "before", "after", "exit"]


def test_async_exception_is_recorded() -> None:
    span = MagicMock()
    tracer = MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = span
    tracer.start_as_current_span.return_value.__exit__.return_value = False
    error = ValueError("business error")

    with (
        patch.object(tracing, "OTEL_AVAILABLE", True),
        patch.object(tracing, "get_tracer", return_value=tracer),
    ):

        @tracing.trace_task("execute")
        async def operation() -> None:
            await asyncio.sleep(0)
            raise error

        with pytest.raises(ValueError) as caught:
            asyncio.run(operation())

    assert caught.value is error
    span.record_exception.assert_called_once_with(error)
    tracer.start_as_current_span.assert_called_once_with(
        "task.execute",
        attributes={"nova.component": "task", "nova.task.action": "execute"},
    )
    if tracing.OtelStatusCode is not None:
        assert span.set_status.call_args.args[0].status_code == tracing.OtelStatusCode.ERROR


def test_no_otel_preserves_async_function_identity() -> None:
    async def operation() -> int:
        return 42

    with patch.object(tracing, "OTEL_AVAILABLE", False):
        assert tracing.trace_task()(operation) is operation

    assert asyncio.run(operation()) == 42


def test_cancellation_closes_span_and_propagates() -> None:
    events: list[str] = []

    @contextmanager
    def span_context(*args, **kwargs) -> Generator[None, None, None]:
        events.append("enter")
        try:
            yield None
        finally:
            events.append("exit")

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        @tracing.trace_task()
        async def operation() -> None:
            started.set()
            await release.wait()

        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert events == ["enter"]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert events == ["enter", "exit"]

    with (
        patch.object(tracing, "OTEL_AVAILABLE", True),
        patch.object(tracing, "nova_span", span_context),
    ):
        asyncio.run(scenario())

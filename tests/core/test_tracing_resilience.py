"""Telemetry failures must not replace application outcomes."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nova.core import tracing


@pytest.fixture
def telemetry(monkeypatch):
    api = MagicMock()
    tracer = api.get_tracer.return_value
    context = tracer.start_as_current_span.return_value
    span = context.__enter__.return_value
    context.__exit__.return_value = False
    status = MagicMock()

    monkeypatch.setattr(tracing, "OTEL_AVAILABLE", True)
    monkeypatch.setattr(tracing, "trace", api)
    monkeypatch.setattr(tracing, "OtelStatus", status)
    monkeypatch.setattr(tracing, "OtelStatusCode", MagicMock())

    return api, tracer, context, span, status


def invoke(mode, operation):
    if mode == "sync":
        return tracing.trace_task()(operation)()

    @tracing.trace_task()
    async def wrapped():
        await asyncio.sleep(0)
        return operation()

    return asyncio.run(wrapped())


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("business_failure", [False, True], ids=["success", "error"])
@pytest.mark.parametrize(
    "stage",
    ["get", "start", "enter", "status_construct", "status", "exit"],
)
def test_telemetry_failure_preserves_outcome(telemetry, mode, business_failure, stage):
    api, tracer, context, span, status = telemetry
    targets = {
        "get": api.get_tracer,
        "start": tracer.start_as_current_span,
        "enter": context.__enter__,
        "status_construct": status,
        "status": span.set_status,
        "exit": context.__exit__,
    }
    targets[stage].side_effect = RuntimeError("telemetry failure")
    error = ValueError("business failure")
    result = object()
    calls = []

    def operation():
        calls.append("executed")
        if business_failure:
            raise error
        return result

    if business_failure:
        with pytest.raises(ValueError) as caught:
            invoke(mode, operation)
        assert caught.value is error
    else:
        assert invoke(mode, operation) is result

    assert calls == ["executed"]
    if stage in {"get", "start", "enter"}:
        context.__exit__.assert_not_called()
    else:
        context.__exit__.assert_called_once()
        exit_args = context.__exit__.call_args.args
        if business_failure:
            assert exit_args[0] is ValueError
            assert exit_args[1] is error
            assert exit_args[2] is not None
        else:
            assert exit_args == (None, None, None)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_record_failure_still_attempts_status_and_exit(telemetry, mode):
    _, _, context, span, _ = telemetry
    span.record_exception.side_effect = RuntimeError("recording failed")
    span.set_status.side_effect = RuntimeError("status failed")
    context.__exit__.side_effect = RuntimeError("exit failed")
    error = ValueError("original")

    def operation():
        raise error

    with pytest.raises(ValueError) as caught:
        invoke(mode, operation)

    assert caught.value is error
    span.record_exception.assert_called_once_with(error)
    span.set_status.assert_called_once()
    context.__exit__.assert_called_once()


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_provider_cannot_suppress_business_error(telemetry, mode):
    _, _, context, _, _ = telemetry
    context.__exit__.return_value = True
    error = ValueError("original")

    def operation():
        raise error

    with pytest.raises(ValueError) as caught:
        invoke(mode, operation)

    assert caught.value is error


@pytest.mark.parametrize("exit_failure", [False, True])
def test_cancelled_task_propagates_with_cleanup(telemetry, exit_failure):
    _, _, context, span, _ = telemetry
    if exit_failure:
        context.__exit__.side_effect = RuntimeError("cleanup failed")

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        @tracing.trace_task()
        async def operation():
            started.set()
            await release.wait()

        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    context.__exit__.assert_called_once()
    assert context.__exit__.call_args.args[0] is asyncio.CancelledError
    span.set_status.assert_not_called()


def test_failed_get_tracer_returns_none(telemetry):
    api, _, _, _, _ = telemetry
    api.get_tracer.side_effect = RuntimeError("provider failed")
    assert tracing.get_tracer() is None
    with tracing.nova_span("fallback") as span:
        assert span is None

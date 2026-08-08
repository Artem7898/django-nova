"""Tests for Task engine instrumentation."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from nova.tasks.engine import NovaTaskEngine


@pytest.fixture
def mock_span():
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    return span


@patch("nova.tasks.backends.asyncio_backend.nova_span")
@pytest.mark.asyncio
async def test_task_submit_and_execute_tracing(mock_nova_span, mock_span):
    mock_nova_span.return_value = mock_span

    engine = NovaTaskEngine(max_concurrent=1)
    await engine.start()

    async def dummy_task():
        return 42

    # Submit
    task_id = engine.submit(dummy_task)

    # Check submit span
    mock_nova_span.assert_called_with("nova.task.submit", task_name="dummy_task")
    mock_span.set_attribute.assert_any_call("task.id", task_id)

    # Wait for execution
    await asyncio.sleep(0.1)

    # Check execution span (called inside worker)
    calls = mock_nova_span.call_args_list
    exec_call = next(c for c in calls if c[0][0] == "nova.task.execute")
    assert exec_call[1]["task_id"] == task_id

    # Verify status attribute was set
    mock_span.set_attribute.assert_any_call("task.status", "SUCCESS")
    mock_span.set_attribute.assert_any_call("task.execution_time_ms", pytest.approx(0, abs=100))

    await engine.stop()

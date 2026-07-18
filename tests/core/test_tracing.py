"""
Tests for distributed tracing layer.
Covers Safe Import, Full Lifecycle, and Decorators.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from nova.core.tracing import (
    OTEL_AVAILABLE,
    get_tracer,
    nova_span,
    trace_cache,
    trace_model,
    trace_task,
    trace_validation,
)


class TestTracingSafeImport:
    """Test suite for the Zero-Overhead Safe Import pattern."""

    def test_otel_available_flag_is_bool(self) -> None:
        assert isinstance(OTEL_AVAILABLE, bool)

    def test_get_tracer_returns_none_if_missing(self) -> None:
        with (
            patch("nova.core.tracing.trace", None),
            patch("nova.core.tracing.OTEL_AVAILABLE", False),
        ):
            assert get_tracer() is None

    def test_nova_span_yields_none_if_missing(self) -> None:
        with (
            patch("nova.core.tracing.trace", None),
            patch("nova.core.tracing.OTEL_AVAILABLE", False),
            nova_span("test.span") as span,
        ):
            assert span is None

    def test_decorators_do_not_wrap_if_missing(self) -> None:
        """If OTEL is missing, decorator must return the original function."""
        with (
            patch("nova.core.tracing.trace", None),
            patch("nova.core.tracing.OTEL_AVAILABLE", False),
        ):
            @trace_model("save")
            def my_func():
                return "original"

            assert my_func() == "original"
            assert my_func.__name__ == "my_func"  # functools.wraps shouldn't break


@pytest.mark.skipif(not OTEL_AVAILABLE, reason="Requires opentelemetry-api installed")
class TestNovaSpanLifecycle:
    """Tests the exact OTEL lifecycle requested in the task."""

    def _setup_mock_span(self) -> tuple[MagicMock, MagicMock]:
        """Helper to setup mock tracer and span with context manager protocol."""
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        return mock_span, mock_tracer

    def test_success_lifecycle_sets_ok_status(self) -> None:
        """Test path: yield -> else -> set_status(OK)"""
        mock_span, mock_tracer = self._setup_mock_span()

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            with nova_span("success.op") as span:
                assert span is not None

        # Verify OK status was set
        mock_span.set_status.assert_called_once()
        status_arg = mock_span.set_status.call_args[0][0]
        assert status_arg.status_code.name == "OK"
        # Verify no exceptions were recorded
        mock_span.record_exception.assert_not_called()

    def test_exception_lifecycle_records_and_sets_error(self) -> None:
        """Test path: yield -> exception -> record_exception -> set_status(ERROR) -> raise"""
        mock_span, mock_tracer = self._setup_mock_span()
        test_error = ValueError("DB Constraint failed")

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            with pytest.raises(ValueError, match="DB Constraint failed"):
                with nova_span("fail.op") as span:
                    assert span is not None
                    raise test_error

        # Verify Error status and exception recording
        mock_span.record_exception.assert_called_once_with(test_error)
        mock_span.set_status.assert_called_once()
        status_arg = mock_span.set_status.call_args[0][0]
        assert status_arg.status_code.name == "ERROR"
        assert "DB Constraint failed" in status_arg.description


@pytest.mark.skipif(not OTEL_AVAILABLE, reason="Requires opentelemetry-api installed")
class TestTracingDecorators:
    """Tests specific component decorators."""

    def _setup_mock_span(self) -> tuple[MagicMock, MagicMock]:
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        return mock_span, mock_tracer

    def test_trace_model_decorator(self) -> None:
        mock_span, mock_tracer = self._setup_mock_span()

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            @trace_model(operation="save")
            def save_article():
                return "saved"

            result = save_article()

            assert result == "saved"
            # Check that the span was created with correct component attributes
            call_kwargs = mock_tracer.start_as_current_span.call_args[1]
            assert call_kwargs["attributes"]["nova.component"] == "model"
            assert call_kwargs["attributes"]["nova.model.action"] == "save"

    def test_trace_cache_decorator(self) -> None:
        mock_span, mock_tracer = self._setup_mock_span()

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            @trace_cache(operation="invalidate")
            def clear_cache():
                pass

            clear_cache()

            call_kwargs = mock_tracer.start_as_current_span.call_args[1]
            assert call_kwargs["attributes"]["nova.component"] == "cache"

    def test_trace_validation_decorator_passes_extra_attrs(self) -> None:
        mock_span, mock_tracer = self._setup_mock_span()

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            @trace_validation(schema_name="ArticleSchema")
            def validate():
                return True

            validate()

            call_kwargs = mock_tracer.start_as_current_span.call_args[1]
            assert call_kwargs["attributes"]["schema"] == "ArticleSchema"

    def test_decorator_inherits_exception_lifecycle(self) -> None:
        """Ensure decorators automatically handle the ERROR lifecycle."""
        mock_span, mock_tracer = self._setup_mock_span()

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            @trace_task(operation="heavy_job")
            def run_task():
                raise RuntimeError("Task crashed")

            with pytest.raises(RuntimeError):
                run_task()

            # Even though it's a decorator, nova_span inside it must record the error
            mock_span.record_exception.assert_called_once()
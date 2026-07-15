"""Tests for distributed tracing layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nova.core.tracing import OTEL_AVAILABLE, get_tracer, nova_span


class TestTracingSafeImport:
    """Test suite for the Safe Import pattern."""

    def test_otel_available_flag(self) -> None:
        """OTEL_AVAILABLE must be a boolean."""
        assert isinstance(OTEL_AVAILABLE, bool)

    def test_get_tracer_returns_none_if_mocked_missing(self) -> None:
        """If OTEL is not installed, get_tracer must return None."""
        # Искусственно эмулируем отсутствие модуля
        with patch("nova.core.tracing.trace", None):
            with patch("nova.core.tracing.OTEL_AVAILABLE", False):
                assert get_tracer() is None

    def test_nova_span_yields_none_if_mocked_missing(self) -> None:
        """nova_span must yield None and not crash if OTEL is missing."""
        with patch("nova.core.tracing.trace", None):
            with patch("nova.core.tracing.OTEL_AVAILABLE", False):
                with nova_span("test.span") as span:
                    assert span is None


@pytest.mark.skipif(not OTEL_AVAILABLE, reason="Requires opentelemetry-api installed")
class TestTracingIntegration:
    """Tests that actually touch the OTEL API (only run if installed)."""

    def test_get_tracer_returns_mocked_tracer(self) -> None:
        """If OTEL is installed, get_tracer must call trace.get_tracer."""
        mock_tracer = MagicMock()
        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer) as mock_get:
            result = get_tracer("nova.test")

            mock_get.assert_called_once_with("nova.test")
            assert result == mock_tracer

    def test_nova_span_creates_span_with_attributes(self) -> None:
        """nova_span must pass attributes to the OTEL span."""
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        # Имитируем поведение контекстного менеджера OTEL
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        with patch("nova.core.tracing.trace.get_tracer", return_value=mock_tracer):
            with nova_span("model.save", model="Article", pk=42) as span:
                # Проверяем, что span создался и мы можем ставить атрибуты
                assert span is not None
                span.set_attribute("status", "success")

            # Проверяем, что OTEL метод был вызван с правильными аргументами
            mock_tracer.start_as_current_span.assert_called_once_with(
                "model.save", attributes={"model": "Article", "pk": 42}
            )
            mock_span.set_attribute.assert_called_once_with("status", "success")

"""
Distributed Tracing layer for Django Nova using OpenTelemetry.
Implements the "Safe Import" pattern: if opentelemetry is not installed,
all tracing functions become no-ops (do nothing) with zero overhead.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

# Safe Import: Если пакета нет, подменяем на пустышки
try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Tracer

    OTEL_AVAILABLE = True
except ImportError:
    trace = None
    Span = None
    Tracer = None
    OTEL_AVAILABLE = False


def get_tracer(name: str = "nova.core") -> Tracer | None:
    """
    Returns an OpenTelemetry Tracer instance if OTEL is configured.
    Returns None otherwise.
    """
    if not OTEL_AVAILABLE or not trace:
        return None
    return trace.get_tracer(name)


@contextmanager
def nova_span(name: str, **attributes: Any) -> Generator[Span | None, None, None]:
    """
    Context manager to create a Nova trace span.
    Safely yields None if OpenTelemetry is not installed.

    Example:
        with nova_span("model.save", model="Article", pk=42) as span:
            # ... do work ...
            if span:
                span.set_attribute("validation.status", "success")
    """
    tracer = get_tracer()
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span

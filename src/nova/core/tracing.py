"""
Distributed Tracing layer for Django Nova using OpenTelemetry.
Architecture: Implements the "Safe Import" pattern with full OTEL lifecycle.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Generator
from contextlib import contextmanager  # <-- ВОТ ЭТА СТРОКА БЫЛА ПОТЕРЯНА
from typing import Any, ParamSpec, TypeVar

# --- Safe Import Block (Architecture Freeze compliant) ---
try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Status, StatusCode, Tracer

    _otel_available = True
except ImportError:
    # Pyright stubs to prevent cascade `None` type failures
    trace = None  # type: ignore[assignment, misc]
    Span = object  # type: ignore[assignment, misc]
    Tracer = object  # type: ignore[assignment, misc]
    Status = object  # type: ignore[assignment, misc]
    StatusCode = object  # type: ignore[assignment, misc]
    _otel_available = False

# --- Generic types for decorators ---
P = ParamSpec("P")
R = TypeVar("R")


def get_tracer(name: str = "nova.core") -> Tracer | None:  # type: ignore[valid-type]
    """Returns an OpenTelemetry Tracer instance or None."""
    if not _otel_available or not trace:
        return None
    return trace.get_tracer(name)


@contextmanager
def nova_span(name: str, **attributes: Any) -> Generator[Span | None, None, None]:  # type: ignore[valid-type]
    """Context manager implementing the full OTEL span lifecycle."""
    tracer = get_tracer()
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as exc:
            if span is not None:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            if span is not None:
                span.set_status(Status(StatusCode.OK))


def _trace_decorator(component: str, action: str, **extra_attrs: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Internal helper to generate typed decorators."""
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if not _otel_available:
            return func

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            span_name = f"{component}.{action}"
            attrs = {"nova.component": component, f"nova.{component}.action": action}
            attrs.update(extra_attrs)

            with nova_span(span_name, **attrs):
                return func(*args, **kwargs)
        return wrapper
    return decorator


# --- Public Specific Decorators ---

def trace_model(operation: str = "execute") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Django Model operations."""
    return _trace_decorator(component="model", action=operation)

def trace_cache(operation: str = "get") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Cache operations."""
    return _trace_decorator(component="cache", action=operation)

def trace_task(task_name: str = "run") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Background Task execution."""
    return _trace_decorator(component="task", action=task_name)

def trace_validation(schema_name: str = "unknown") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Pydantic validation layer."""
    return _trace_decorator(component="validation", action="validate", schema=schema_name)


# Backward compatibility alias for tests
OTEL_AVAILABLE = _otel_available

__all__ = [
    "OTEL_AVAILABLE",
    "get_tracer",
    "nova_span",
    "trace_cache",
    "trace_model",
    "trace_task",
    "trace_validation",
]
"""Distributed Tracing layer for Django Nova using OpenTelemetry.
Architecture: Implements the "Safe Import" pattern with full OTEL lifecycle.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

# --- Safe Import Block (Architecture Freeze compliant) ---

class _StubSpan:
    """Fallback stub for OpenTelemetry Span when OTEL is not installed."""
    def set_attribute(self, key: str, value: Any) -> None: ...
    def record_exception(self, exc: BaseException) -> None: ...
    def set_status(self, status: Any) -> None: ...

try:
    import opentelemetry.trace as trace
    _otel_available = True
except ImportError:
    # Explicitly annotate as Any directly in except to completely prevent Unknown cascading
    trace: Any = None
    _otel_available = False

# Any aliases break the Unknown chain completely
Span: Any = _StubSpan
Tracer: Any = object
Status: Any = object
StatusCode: Any = object

# --- Generic types for decorators ---
P = ParamSpec("P")
R = TypeVar("R")


def get_tracer(name: str = "nova.core") -> Any:
    """Returns an OpenTelemetry Tracer instance or None."""
    if not _otel_available or not trace:
        return None
    return trace.get_tracer(name)


@contextmanager
def nova_span(name: str, **attributes: Any) -> Generator[Any, None, None]:
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
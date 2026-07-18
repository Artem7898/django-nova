"""
Distributed Tracing layer for Django Nova using OpenTelemetry.

Architecture: Implements the "Safe Import" pattern with full OTEL lifecycle.
If opentelemetry is not installed, all functions become no-ops with ~0ns overhead.

Lifecycle:
1. Create span
2. Write initial attributes
3. Yield span to business logic
4. If exception -> record_exception() -> set_status(ERROR) -> raise
5. If success -> set_status(OK)
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

# --- Safe Import Block ---
try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Status, StatusCode, Tracer

    OTEL_AVAILABLE = True
except ImportError:
    trace = None
    Span = None  # type: ignore
    Tracer = None  # type: ignore
    Status = None  # type: ignore
    StatusCode = None  # type: ignore
    OTEL_AVAILABLE = False

# --- Generic types for decorators ---
P = ParamSpec("P")
R = TypeVar("R")


def get_tracer(name: str = "nova.core") -> Tracer | None:
    """Returns an OpenTelemetry Tracer instance or None."""
    if not OTEL_AVAILABLE or not trace:
        return None
    return trace.get_tracer(name)


@contextmanager
def nova_span(name: str, **attributes: Any) -> Generator[Span | None, None, None]:
    """
    Context manager implementing the full OTEL span lifecycle.
    Safely yields None if OTEL is missing.
    """
    tracer = get_tracer()
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            # 3. Yield span
            yield span
        except Exception as exc:
            # 4. Exception handling lifecycle
            if span is not None:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            # Re-raise to not swallow business logic errors
            raise
        else:
            # 5. Success lifecycle
            if span is not None:
                span.set_status(Status(StatusCode.OK))


def _trace_decorator(component: str, action: str, **extra_attrs: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Internal helper to generate typed decorators for specific Nova components.
    Keeps the code DRY and ensures consistent attribute naming.
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        # Fast path for no-OTEL environments
        if not OTEL_AVAILABLE:
            return func

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            span_name = f"{component}.{action}"
            # Add component context to attributes
            attrs = {"nova.component": component, f"nova.{component}.action": action}
            attrs.update(extra_attrs)

            with nova_span(span_name, **attrs):
                return func(*args, **kwargs)
        return wrapper
    return decorator


# --- Public Specific Decorators ---

def trace_model(operation: str = "execute") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Django Model operations (save, delete, etc.)."""
    return _trace_decorator(component="model", action=operation)

def trace_cache(operation: str = "get") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Cache operations (get, set, invalidate)."""
    return _trace_decorator(component="cache", action=operation)

def trace_task(task_name: str = "run") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Background Task execution."""
    return _trace_decorator(component="task", action=task_name)

def trace_validation(schema_name: str = "unknown") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Pydantic validation layer."""
    return _trace_decorator(component="validation", action="validate", schema=schema_name)


__all__ = [
    "OTEL_AVAILABLE",
    "get_tracer",
    "nova_span",
    "trace_cache",
    "trace_model",
    "trace_task",
    "trace_validation",
]
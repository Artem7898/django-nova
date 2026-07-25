"""
Distributed Tracing layer for Django Nova using OpenTelemetry.
Architecture: Implements the "Safe Import" pattern with full OTEL lifecycle.
Automatically injects Distributed Context (Correlation IDs) into spans.
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
    Automatically injects variables from nova.context into span attributes.
    """
    tracer = get_tracer()
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span(name, attributes=attributes) as span:
        # --- NOVA INNOVATION: Inject Distributed Context ---
        if span is not None:
            try:
                from nova.core.context import get_all
                ctx_data = get_all()
                if ctx_data:
                    span.set_attributes(ctx_data)
            except Exception:
                pass # Context module not available or misconfigured
        # ----------------------------------------------------

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
        if not OTEL_AVAILABLE:
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
    return _trace_decorator(component="model", action=operation)

def trace_cache(operation: str = "get") -> Callable[[Callable[P, R]], Callable[P, R]]:
    return _trace_decorator(component="cache", action=operation)

def trace_task(task_name: str = "run") -> Callable[[Callable[P, R]], Callable[P, R]]:
    return _trace_decorator(component="task", action=task_name)

def trace_validation(schema_name: str = "unknown") -> Callable[[Callable[P, R]], Callable[P, R]]:
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
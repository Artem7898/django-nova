# NOVA_TRACING_V2
"""Distributed tracing layer for Django Nova using OpenTelemetry.

The tracing layer is intentionally optional:

- OpenTelemetry may be unavailable.
- Tracing must never break business operations.
- When OpenTelemetry is unavailable, ``nova_span()`` yields ``None``.
- Public APIs remain strongly typed without leaking OpenTelemetry's
  optional dependency into the rest of Nova.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, ParamSpec, TypeVar

if TYPE_CHECKING:
    from opentelemetry import trace as trace
    from opentelemetry.trace import Span, Tracer

P = ParamSpec("P")
R = TypeVar("R")

SpanAttribute = str | int | float | bool
SpanValue = SpanAttribute | None


# ---------------------------------------------------------------------------
# Optional OpenTelemetry boundary
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status as OtelStatus
    from opentelemetry.trace import StatusCode as OtelStatusCode

    _otel_available = True
except ImportError:
    trace = None
    OtelStatus = None
    OtelStatusCode = None
    _otel_available = False


OTEL_AVAILABLE: bool = _otel_available


def get_tracer(name: str = "nova") -> Tracer | None:
    """
    Return an OpenTelemetry tracer when available.

    The optional dependency is isolated inside this function. Nova callers
    only see ``Tracer | None`` and never need to know how OpenTelemetry
    was imported.
    """
    if not OTEL_AVAILABLE or trace is None:
        return None

    return trace.get_tracer(name)


@contextmanager
def nova_span(
    name: str,
    **attributes: SpanValue,
) -> Generator[Span | None, None, None]:
    """
    Create and manage an OpenTelemetry span.

    When OpenTelemetry is unavailable, the context manager becomes a
    no-op and yields ``None``.

    Exceptions raised inside the context are recorded on the span and
    re-raised unchanged. Tracing therefore never masks business errors.
    """
    tracer = get_tracer(name)

    if tracer is None:
        yield None
        return

    otel_attributes: dict[str, SpanAttribute] = {
        key: value for key, value in attributes.items() if value is not None
    }

    with tracer.start_as_current_span(
        name,
        attributes=otel_attributes,
    ) as span:
        try:
            yield span

        except Exception as exc:
            span.record_exception(exc)

            if OtelStatus is not None and OtelStatusCode is not None:
                span.set_status(
                    OtelStatus(
                        OtelStatusCode.ERROR,
                        str(exc),
                    ),
                )

            raise

        else:
            if OtelStatus is not None and OtelStatusCode is not None:
                span.set_status(
                    OtelStatus(
                        OtelStatusCode.OK,
                    ),
                )


# ---------------------------------------------------------------------------
# Typed decorators
# ---------------------------------------------------------------------------


def _trace_decorator(
    component: str,
    action: str,
    **extra_attrs: SpanAttribute,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Build a strongly typed tracing decorator."""

    def decorator(
        func: Callable[P, R],
    ) -> Callable[P, R]:
        if not OTEL_AVAILABLE:
            return func

        @functools.wraps(func)
        def wrapper(
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> R:
            span_name = f"{component}.{action}"

            attributes: dict[str, SpanAttribute] = {
                "nova.component": component,
                f"nova.{component}.action": action,
            }
            attributes.update(extra_attrs)

            with nova_span(span_name, **attributes):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def trace_model(
    operation: str = "execute",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Django model operations."""
    return _trace_decorator(
        component="model",
        action=operation,
    )


def trace_cache(
    operation: str = "get",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace cache operations."""
    return _trace_decorator(
        component="cache",
        action=operation,
    )


def trace_task(
    operation: str = "run",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace background task execution."""
    return _trace_decorator(
        component="task",
        action=operation,
    )


def trace_validation(
    schema_name: str = "unknown",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace Pydantic validation."""
    return _trace_decorator(
        component="validation",
        action="validate",
        schema=schema_name,
    )


__all__ = [
    "OTEL_AVAILABLE",
    "get_tracer",
    "nova_span",
    "trace_cache",
    "trace_model",
    "trace_task",
    "trace_validation",
]

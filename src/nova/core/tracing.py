# NOVA_TRACING_V2
"""Distributed tracing layer for Django Nova using OpenTelemetry.

The tracing layer is intentionally optional:

- OpenTelemetry may be unavailable.
- Ordinary telemetry exceptions do not replace business results or errors.
- When OpenTelemetry is unavailable, ``nova_span()`` yields ``None``.
- Public APIs remain strongly typed without leaking OpenTelemetry's
  optional dependency into the rest of Nova.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable, Generator
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

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

    try:
        return trace.get_tracer(name)
    except Exception:
        return None


@contextmanager
def nova_span(
    name: str,
    **attributes: SpanValue,
) -> Generator[Span | None, None, None]:
    """Trace an operation without letting ordinary telemetry errors escape.

    Failed setup falls back to yielding None. Recording and teardown are
    best-effort. Business exceptions are always re-raised, even when the
    provider context manager requests suppression or fails during exit.
    Cancellation and other BaseException subclasses from the body propagate.

    This boundary guards Nova's telemetry calls, not direct span method calls
    made by application code. Provider BaseException signals are not suppressed.
    """
    span_context: AbstractContextManager[Span] | None = None
    span: Span | None = None

    try:
        tracer = get_tracer(name)
        if tracer is not None:
            otel_attributes: dict[str, SpanAttribute] = {
                key: value for key, value in attributes.items() if value is not None
            }
            span_context = tracer.start_as_current_span(
                name,
                attributes=otel_attributes,
            )
            span = span_context.__enter__()
    except Exception:
        # Do not enclose the business yield in this fallback handler:
        # otherwise a body exception could cause a second execution.
        span_context = None

    if span_context is None:
        yield None
        return

    try:
        yield span
    except BaseException as exc:
        if span is not None and isinstance(exc, Exception):
            with suppress(Exception):
                span.record_exception(exc)
            with suppress(Exception):
                if OtelStatus is not None and OtelStatusCode is not None:
                    span.set_status(OtelStatus(OtelStatusCode.ERROR, str(exc)))

        with suppress(Exception):
            # Ignore a provider's suppression request: business errors belong
            # to the caller, not to the telemetry implementation.
            span_context.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with suppress(Exception):
            if span is not None and OtelStatus is not None and OtelStatusCode is not None:
                span.set_status(OtelStatus(OtelStatusCode.OK))
        with suppress(Exception):
            span_context.__exit__(None, None, None)


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

        span_name = f"{component}.{action}"
        attributes: dict[str, SpanAttribute] = {
            "nova.component": component,
            f"nova.{component}.action": action,
        }
        attributes.update(extra_attrs)

        if inspect.iscoroutinefunction(func):
            async_func = cast(Callable[P, Awaitable[object]], func)

            @functools.wraps(func)
            async def async_wrapper(
                *args: P.args,
                **kwargs: P.kwargs,
            ) -> object:
                with nova_span(span_name, **attributes):
                    return await async_func(*args, **kwargs)

            # Runtime detection selects the coroutine branch; preserve the
            # original callable's parameter and return types for callers.
            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def wrapper(
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> R:
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

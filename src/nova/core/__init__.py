"""Core Abstractions: Observability, Tracing, Exceptions, Context."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "NovaValidationError",
    "bind",
    "clear",
    "get_all",
    "new_context",
    "setup_observability",
    "trace_cache",
    "trace_model",
    "trace_task",
    "trace_validation",
    "unbind",
]

def __getattr__(name: str):
    if name == "NovaValidationError":
        from nova.core.exceptions import NovaValidationError
        return NovaValidationError
    if name == "setup_observability":
        from nova.core.observability import setup_nova_logging as setup_observability
        return setup_observability
    if name == "trace_model":
        from nova.core.tracing import trace_model
        return trace_model
    if name == "trace_cache":
        from nova.core.tracing import trace_cache
        return trace_cache
    if name == "trace_validation":
        from nova.core.tracing import trace_validation
        return trace_validation
    if name == "trace_task":
        from nova.core.tracing import trace_task
        return trace_task
    # Context management
    if name == "bind":
        from nova.core.context import bind
        return bind
    if name == "unbind":
        from nova.core.context import unbind
        return unbind
    if name == "clear":
        from nova.core.context import clear
        return clear
    if name == "get_all":
        from nova.core.context import get_all
        return get_all
    if name == "new_context":
        from nova.core.context import new_context
        return new_context

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.core.context import bind, clear, get_all, new_context, unbind
    from nova.core.exceptions import NovaValidationError
    from nova.core.observability import setup_nova_logging as setup_observability
    from nova.core.tracing import trace_cache, trace_model, trace_task, trace_validation
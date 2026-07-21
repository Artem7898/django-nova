"""Core Abstractions: Observability, Tracing, Exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "NovaValidationError",
    "setup_observability",
    "trace_cache",
    "trace_model",
    "trace_validation",
]

def __getattr__(name: str):
    if name == "NovaValidationError":
        from nova.core.exceptions import NovaValidationError
        return NovaValidationError
    if name == "setup_observability":
        from nova.core.observability import setup_observability
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.core.exceptions import NovaValidationError
    from nova.core.observability import setup_observability
    from nova.core.tracing import trace_cache, trace_model, trace_validation
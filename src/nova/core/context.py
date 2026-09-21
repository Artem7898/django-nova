"""
Context-local metadata and a best-effort structlog bridge.

Bindings follow Python's contextvars propagation rules. Queue, process, and
network boundaries require explicit propagation by the application.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

# The underlying contextvar holding a dictionary of all active context data.
# Default is None to satisfy Ruff B039 (no mutable defaults).
_nova_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "nova_context", default=None
)

# Standard key for correlation IDs in Nova
CORRELATION_ID_KEY = "correlation_id"


def _get_ctx() -> dict[str, Any]:
    """Internal helper to safely get the context dict."""
    ctx = _nova_context.get()
    return ctx if ctx is not None else {}


def bind(**kwargs: Any) -> None:
    """Merge bindings in the current context, copying the top-level mapping."""
    ctx = _get_ctx().copy()
    ctx.update(kwargs)
    _nova_context.set(ctx)
    _sync_structlog_context(ctx)


def unbind(*keys: str) -> None:
    """Remove keys in the current context; missing keys are ignored."""
    ctx = _get_ctx().copy()
    for key in keys:
        ctx.pop(key, None)
    _nova_context.set(ctx)
    _sync_structlog_context(ctx)


def clear() -> None:
    """Clear bindings in the current context, without changing other tasks."""
    _nova_context.set(None)
    _sync_structlog_context({})


def get(key: str, default: Any = None) -> Any:
    """Get a specific context variable."""
    return _get_ctx().get(key, default)


def get_all() -> dict[str, Any]:
    """Return a shallow copy; mutable values are still shared references."""
    return _get_ctx().copy()


@contextmanager
def new_context(**kwargs: Any) -> Generator[None, None, None]:
    """Temporarily replace all bindings, then restore the enclosing context.

    Use ``with`` inside sync or async functions. Restoration also runs when
    the body raises or is cancelled; values are not deep-copied.
    """
    token = _nova_context.set(kwargs.copy())
    try:
        _sync_structlog_context(_get_ctx())
        yield
    finally:
        _nova_context.reset(token)
        _sync_structlog_context(_get_ctx())


def _sync_structlog_context(ctx: dict[str, Any]) -> None:
    """Replace structlog contextvars with Nova bindings, if available."""
    try:
        import structlog

        structlog.contextvars.clear_contextvars()
        if ctx:
            structlog.contextvars.bind_contextvars(**ctx)
    except Exception:
        pass

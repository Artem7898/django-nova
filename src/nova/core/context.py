"""
Distributed Context Management.
Uses contextvars to transparently pass Correlation IDs across logs, traces, and tasks.
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
    """Bind key-value pairs to the current distributed context."""
    ctx = _get_ctx().copy()
    ctx.update(kwargs)
    _nova_context.set(ctx)
    _sync_structlog_context(ctx)


def unbind(*keys: str) -> None:
    """Remove specific keys from the current distributed context."""
    ctx = _get_ctx().copy()
    for key in keys:
        ctx.pop(key, None)
    _nova_context.set(ctx)
    _sync_structlog_context(ctx)


def clear() -> None:
    """Wipe the context to prevent leaks in thread pools or async loops."""
    _nova_context.set(None)
    _sync_structlog_context({})


def get(key: str, default: Any = None) -> Any:
    """Get a specific context variable."""
    return _get_ctx().get(key, default)


def get_all() -> dict[str, Any]:
    """Return a copy of the current context for serialization."""
    return _get_ctx().copy()


@contextmanager
def new_context(**kwargs: Any) -> Generator[None, None, None]:
    """Context manager to temporarily bind variables."""
    prev_ctx = get_all()
    try:
        clear()
        bind(**kwargs)
        yield
    finally:
        clear()
        if prev_ctx:
            bind(**prev_ctx)


def _sync_structlog_context(ctx: dict[str, Any]) -> None:
    """Pushes current context dict into structlog's internal contextvars."""
    try:
        import structlog

        structlog.contextvars.clear_contextvars()
        if ctx:
            structlog.contextvars.bind_contextvars(**ctx)
    except Exception:
        pass

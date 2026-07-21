"""Async ORM wrappers (Experimental)."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["AsyncTypedManager", "AsyncTypedQuerySet"]

def __getattr__(name: str):
    if name == "AsyncTypedQuerySet":
        from nova.async_orm.queryset import AsyncTypedQuerySet
        return AsyncTypedQuerySet
    if name == "AsyncTypedManager":
        from nova.async_orm.manager import AsyncTypedManager
        return AsyncTypedManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.async_orm.manager import AsyncTypedManager
    from nova.async_orm.queryset import AsyncTypedQuerySet
"""Async ORM wrappers (Experimental for Django 5.1+)."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["AsyncNovaManager", "AsyncTypedQuerySet"]

def __getattr__(name: str):
    if name == "AsyncTypedQuerySet":
        from nova.async_orm.queryset import AsyncTypedQuerySet
        return AsyncTypedQuerySet
    if name == "AsyncNovaManager":
        from nova.async_orm.manager import AsyncNovaManager
        return AsyncNovaManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.async_orm.manager import AsyncNovaManager
    from nova.async_orm.queryset import AsyncTypedQuerySet
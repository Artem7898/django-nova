"""Pluggable Cache Abstraction Layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "QuerySetCache",
    "connect_invalidation",
    "get_default_cache",
]


def __getattr__(name: str) -> Any:
    if name == "QuerySetCache":
        from nova.cache.queryset_cache import QuerySetCache

        return QuerySetCache
    if name == "connect_invalidation":
        from nova.cache.invalidation import connect_invalidation

        return connect_invalidation

    if name == "get_default_cache":
        from nova.cache.queryset_cache import get_default_cache

        return get_default_cache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from nova.cache.invalidation import connect_invalidation
    from nova.cache.queryset_cache import QuerySetCache, get_default_cache

"""Pluggable Cache Abstraction Layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "QuerySetCache",
    "connect_invalidation",
]


def __getattr__(name: str) -> Any:
    if name == "QuerySetCache":
        from nova.cache.queryset_cache import QuerySetCache

        return QuerySetCache
    if name == "connect_invalidation":
        from nova.cache.invalidation import connect_invalidation

        return connect_invalidation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from nova.cache.invalidation import connect_invalidation
    from nova.cache.queryset_cache import QuerySetCache

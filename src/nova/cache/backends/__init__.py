"""Cache Backend Implementations and Protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "CacheBackend",
    "MemoryCacheBackend",
    "NullCacheBackend",
    "RedisCacheBackend",
]


def __getattr__(name: str) -> Any:
    if name == "CacheBackend":
        from nova.cache.backends.protocol import CacheBackend

        return CacheBackend
    if name == "MemoryCacheBackend":
        from nova.cache.backends.memory import MemoryCacheBackend

        return MemoryCacheBackend
    if name == "NullCacheBackend":
        from nova.cache.backends.null import NullCacheBackend

        return NullCacheBackend
    if name == "RedisCacheBackend":
        from nova.cache.backends.redis import RedisCacheBackend

        return RedisCacheBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from nova.cache.backends.memory import MemoryCacheBackend
    from nova.cache.backends.null import NullCacheBackend
    from nova.cache.backends.protocol import CacheBackend
    from nova.cache.backends.redis import RedisCacheBackend

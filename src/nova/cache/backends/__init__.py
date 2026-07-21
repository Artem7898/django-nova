"""Cache Backend Implementations and Protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "CacheBackend",
    "MemoryCache",
    "NullCache",
    "RedisCache",
]

def __getattr__(name: str):
    if name == "CacheBackend":
        from nova.cache.backends.protocol import CacheBackend
        return CacheBackend
    if name == "MemoryCache":
        from nova.cache.backends.memory import MemoryCache
        return MemoryCache
    if name == "NullCache":
        from nova.cache.backends.null import NullCache
        return NullCache
    if name == "RedisCache":
        from nova.cache.backends.redis_backend import RedisCache
        return RedisCache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.cache.backends.memory import MemoryCache
    from nova.cache.backends.null import NullCache
    from nova.cache.backends.protocol import CacheBackend
    from nova.cache.backends.redis_backend import RedisCache
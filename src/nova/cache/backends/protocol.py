"""
Core cache backend protocol.

Every cache backend in Django Nova must implement this protocol.

The protocol intentionally defines behaviour rather than implementation.
This guarantees that every backend — memory, Redis, Memcached, Django cache,
or null — is interchangeable without changing application code.

Philosophy:
- Transparent Infrastructure
- Type Safety Everywhere
- Stable Public API
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Protocol, runtime_checkable

TTL = int | float | timedelta | None


@runtime_checkable
class CacheBackend(Protocol):
    """
    Infrastructure contract implemented by every synchronous cache backend.
    """

    #
    # Core operations
    #

    def get(self, key: str, default: Any | None = None) -> Any | None: ...

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: TTL = None,
    ) -> None: ...

    def delete(self, key: str) -> bool: ...

    def clear(self) -> None: ...

    #
    # Optional bulk operations
    #

    def get_many(
        self,
        keys: list[str],
    ) -> Mapping[str, Any]: ...

    def set_many(
        self,
        values: Mapping[str, Any],
        *,
        ttl: TTL = None,
    ) -> None: ...

    def delete_many(
        self,
        keys: list[str],
    ) -> int: ...

    #
    # Introspection
    #

    @property
    def backend_name(self) -> str: ...

    @property
    def supports_ttl(self) -> bool: ...

    @property
    def supports_atomic_increment(self) -> bool: ...

    @property
    def supports_pattern_delete(self) -> bool: ...

    def size(self) -> int: ...


class AsyncCacheBackend(Protocol):
    """
    Asynchronous infrastructure contract for cache backends.
    """

    async def get(self, key: str, default: Any | None = None) -> Any | None: ...

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: TTL = None,
    ) -> None: ...

    async def delete(self, key: str) -> bool: ...

    async def clear(self) -> None: ...

    async def get_many(self, keys: list[str]) -> Mapping[str, Any]: ...

    async def set_many(
        self,
        values: Mapping[str, Any],
        *,
        ttl: TTL = None,
    ) -> None: ...

    async def delete_many(self, keys: list[str]) -> int: ...

    @property
    def backend_name(self) -> str: ...

    @property
    def supports_ttl(self) -> bool: ...

    @property
    def supports_atomic_increment(self) -> bool: ...

    @property
    def supports_pattern_delete(self) -> bool: ...

    def size(self) -> int: ...

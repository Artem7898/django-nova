"""
Cache backend protocol used by all Nova cache implementations.

Design goals:
- backend interchangeability
- sync API for Django ORM compatibility
- optional statistics support
- zero dependency on specific cache providers
"""

from __future__ import annotations

from typing import Any, Protocol


class CacheBackend(Protocol):
    """
    Common cache backend interface.
    """

    def get(
        self,
        key: str,
    ) -> Any | None:
        ...

    def set(
        self,
        key: str,
        value: Any,
        ttl: int,
    ) -> None:
        ...

    def delete(
        self,
        key: str,
    ) -> None:
        ...

    def clear(self) -> None:
        ...

    def stats(self) -> dict[str, Any]:
        ...
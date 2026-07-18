"""
No-op cache backend.

Useful for:

- benchmarks
- debugging
- deterministic tests
- disabling cache globally
"""

from __future__ import annotations

from typing import Any

from nova.cache.backends.protocol import CacheBackend


class NullCacheBackend(CacheBackend):
    """
    Cache backend that never stores anything.
    """

    def get(
        self,
        key: str,
    ) -> None:
        return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: int,
    ) -> None:
        return None

    def delete(
        self,
        key: str,
    ) -> None:
        return None

    def clear(self) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "null",
        }
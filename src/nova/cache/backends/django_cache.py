"""
Adapter for Django cache framework.
"""

from __future__ import annotations

from typing import Any

from django.core.cache import caches

from nova.cache.backends.protocol import CacheBackend


class DjangoCacheBackend(CacheBackend):
    """
    Adapter around django.core.cache backend.
    """

    def __init__(
        self,
        alias: str = "default",
    ) -> None:
        self._cache = caches[alias]
        self._alias = alias

    def get(
        self,
        key: str,
    ) -> Any | None:
        return self._cache.get(key)

    def set(
        self,
        key: str,
        value: Any,
        ttl: int,
    ) -> None:
        self._cache.set(
            key,
            value,
            timeout=ttl,
        )

    def delete(
        self,
        key: str,
    ) -> None:
        self._cache.delete(key)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "django",
            "alias": self._alias,
        }
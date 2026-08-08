"""
Adapter for Django cache framework.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from django.core.cache import caches

from .protocol import TTL, CacheBackend


class DjangoCacheBackend(CacheBackend):
    """
    Adapter around Django cache framework.

    This backend adapts Django's cache API to the Nova CacheBackend contract.
    """

    def __init__(self, alias: str = "default") -> None:
        self._cache: Any = caches[alias]
        self._alias = alias

    def _get_timeout(self, ttl: TTL) -> float | None:
        if ttl is None:
            return None

        if isinstance(ttl, timedelta):
            return ttl.total_seconds()

        return float(ttl)

    def get(self, key: str, default: Any | None = None) -> Any | None:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        self._cache.set(key, value, timeout=self._get_timeout(ttl))

    def delete(self, key: str) -> bool:
        return bool(self._cache.delete(key))

    def clear(self) -> None:
        self._cache.clear()

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        data = self._cache.get_many(keys)
        return {key: data.get(key) for key in keys}

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        self._cache.set_many(values, timeout=self._get_timeout(ttl))

    def delete_many(self, keys: list[str]) -> int:
        count = 0

        for key in keys:
            if self.delete(key):
                count += 1

        return count

    @property
    def backend_name(self) -> str:
        return "django"

    @property
    def supports_ttl(self) -> bool:
        return True

    @property
    def supports_atomic_increment(self) -> bool:
        return False

    @property
    def supports_pattern_delete(self) -> bool:
        return False

    def size(self) -> int:
        return -1

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "alias": self._alias,
        }

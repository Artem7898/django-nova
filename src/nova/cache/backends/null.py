"""
Null cache backend.

This backend has no external dependencies and supports no advanced features.
It can be used as a minimal fallback backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any

from .protocol import TTL, CacheBackend

_MISSING = object()


class NullCacheBackend(CacheBackend):
    """
    Minimal cache backend without TTL and advanced operations.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = RLock()

    def get(self, key: str, default: Any | None = None) -> Any | None:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        _ = ttl

        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> bool:
        with self._lock:
            old = self._data.pop(key, _MISSING)
            return old is not _MISSING

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        with self._lock:
            return {key: self._data.get(key) for key in keys}

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        _ = ttl

        with self._lock:
            self._data.update(values)

    def delete_many(self, keys: list[str]) -> int:
        count = 0

        with self._lock:
            for key in keys:
                old = self._data.pop(key, _MISSING)
                if old is not _MISSING:
                    count += 1

        return count

    @property
    def backend_name(self) -> str:
        return "null"

    @property
    def supports_ttl(self) -> bool:
        return False

    @property
    def supports_atomic_increment(self) -> bool:
        return False

    @property
    def supports_pattern_delete(self) -> bool:
        return False

    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "currsize": self.size(),
            "maxsize": None,
            "ttl": None,
        }
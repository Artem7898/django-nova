"""
In-memory cache backend.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock
from typing import Any

from .protocol import TTL, CacheBackend


@dataclass(slots=True)
class _MemoryEntry:
    value: Any
    expires_at: float | None


class MemoryCacheBackend(CacheBackend):
    """
    Default cache backend.

    Supports:
    - per-key TTL
    - maxsize eviction
    - stats introspection
    """

    def __init__(
        self,
        *,
        maxsize: int = 1000,
        ttl: int = 60,
    ) -> None:
        self._data: OrderedDict[str, _MemoryEntry] = OrderedDict()
        self._lock = RLock()
        self._maxsize = maxsize
        self._default_ttl = ttl

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _ttl_seconds(self, ttl: TTL) -> float | None:
        if ttl is None:
            return float(self._default_ttl)

        if isinstance(ttl, timedelta):
            return ttl.total_seconds()

        return float(ttl)

    def _is_expired(self, entry: _MemoryEntry, now: float) -> bool:
        return entry.expires_at is not None and now >= entry.expires_at

    def _purge_expired(self) -> None:
        now = self._now()

        expired_keys = [key for key, entry in self._data.items() if self._is_expired(entry, now)]

        for key in expired_keys:
            del self._data[key]

    def get(self, key: str, default: Any | None = None) -> Any | None:
        with self._lock:
            entry = self._data.get(key)

            if entry is None:
                return default

            now = self._now()

            if self._is_expired(entry, now):
                del self._data[key]
                return default

            self._data.move_to_end(key)
            return entry.value

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        seconds = self._ttl_seconds(ttl)

        with self._lock:
            self._purge_expired()

            expires_at = None if seconds is None else self._now() + seconds

            self._data[key] = _MemoryEntry(
                value=value,
                expires_at=expires_at,
            )

            self._data.move_to_end(key)

            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def delete(self, key: str) -> bool:
        with self._lock:
            entry = self._data.get(key)

            if entry is None:
                return False

            if self._is_expired(entry, self._now()):
                del self._data[key]
                return False

            del self._data[key]
            return True

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        return {key: self.get(key) for key in keys}

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        for key, value in values.items():
            self.set(key, value, ttl=ttl)

    def delete_many(self, keys: list[str]) -> int:
        count = 0

        for key in keys:
            if self.delete(key):
                count += 1

        return count

    @property
    def backend_name(self) -> str:
        return "memory"

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
        with self._lock:
            self._purge_expired()
            return len(self._data)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "currsize": self.size(),
            "maxsize": self._maxsize,
            "ttl": self._default_ttl,
        }

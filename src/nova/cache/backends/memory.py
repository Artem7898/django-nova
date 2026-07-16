from cachetools import TTLCache

from nova.cache.backends.base import CacheBackend


class MemoryBackend(CacheBackend):
    def __init__(
        self,
        *,
        maxsize: int = 1000,
    ) -> None:
        self._cache = TTLCache(maxsize=maxsize, ttl=60)

    def get(self, key: str):
        return self._cache.get(key)

    def set(
        self,
        key: str,
        value,
        ttl: int,
    ) -> None:
        self._cache.ttl = ttl
        self._cache[key] = value

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()
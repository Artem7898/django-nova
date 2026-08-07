from tests.cache.contracts import CacheBackendContract

from nova.cache.backends.null import NullCacheBackend


class TestNullBackend(CacheBackendContract):
    def create_backend(self) -> NullCacheBackend:
        return NullCacheBackend()
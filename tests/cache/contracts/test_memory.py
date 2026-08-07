from tests.cache.contracts import CacheBackendContract

from nova.cache.backends.memory import MemoryCacheBackend


class TestMemoryBackend(CacheBackendContract):
    def create_backend(self) -> MemoryCacheBackend:
        # maxsize побольше, чтобы не мешало тестам
        return MemoryCacheBackend(maxsize=1000, ttl=60)
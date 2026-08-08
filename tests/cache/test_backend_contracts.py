"""Phase B: semantic consistency across cache backends."""

from __future__ import annotations

import pytest
from tests.cache.contracts import CacheBackendContract, CacheBackendExpectation

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.null import NullCacheBackend


@pytest.mark.parametrize(
    "expectation",
    [
        CacheBackendExpectation(target="Memory", factory=MemoryCacheBackend, supports_ttl=True),
        CacheBackendExpectation(target="Null", factory=NullCacheBackend, supports_ttl=False),
    ],
    ids=lambda e: e.target,
)
def test_backend_contract(expectation: CacheBackendExpectation) -> None:
    CacheBackendContract(expectation).run_all()


def test_memory_eviction_on_maxsize() -> None:
    """Memory-specific: LRU eviction when maxsize is exceeded."""
    backend = MemoryCacheBackend(maxsize=2)
    backend.set("a", 1)
    backend.set("b", 2)
    backend.set("c", 3)  # evicts "a"
    assert backend.get("a") is None
    assert backend.get("b") == 2
    assert backend.get("c") == 3

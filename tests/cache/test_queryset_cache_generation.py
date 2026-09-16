"""Cache publication must not cross an invalidation boundary."""

from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache


class QueryStub:
    """Pause logically after reading data but before returning it to the cache."""

    model = SimpleNamespace(
        _meta=SimpleNamespace(app_label="tests", model_name="record"),
    )
    db = "default"
    query = SimpleNamespace(sql_with_params=lambda: ("SELECT value FROM record", ()))

    def __init__(self, value: int, after_read: Callable[[], object]) -> None:
        self.value = value
        self.after_read = after_read

    def __iter__(self) -> Iterator[int]:
        value = self.value
        self.after_read()
        return iter([value])


@pytest.mark.parametrize("operation", ["invalidate", "clear", "shared_invalidate"])
def test_invalidation_fences_unregistered_fill(operation: str) -> None:
    cache = QuerySetCache(backend=MemoryCacheBackend())
    peer = QuerySetCache(_state=cache._state)

    def invalidate() -> None:
        if operation == "clear":
            cache.clear()
        else:
            target = peer if operation == "shared_invalidate" else cache
            assert target.invalidate_model("tests.record") == 0

    old = QueryStub(1, invalidate)
    assert cache.get_or_set(old) == [1]
    fresh = QueryStub(2, lambda: None)
    assert cache.get(fresh) is None
    assert cache.get_or_set(fresh) == [2]
    assert cache.get(fresh) == [2]
    assert peer.invalidate_model("tests.record") == 1
    assert cache.get(fresh) is None

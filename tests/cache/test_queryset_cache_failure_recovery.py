"""Cache invalidation failures must not make stale entries readable again."""

from types import SimpleNamespace

import pytest

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache
from nova.core.exceptions import NovaCacheError


class FailingMemoryBackend(MemoryCacheBackend):
    """A real memory store with deterministic failures at its I/O boundary."""

    def __init__(self):
        super().__init__(maxsize=100, ttl=300)
        self.fail_delete_keys = set()
        self.delete_before_error = False
        self.fail_clear = False
        self.fail_set = False
        self.writes = []
        self.delete_attempts = []

    def set(self, key, value, *, ttl=None):
        if self.fail_set:
            raise NovaCacheError("cache write unavailable")
        super().set(key, value, ttl=ttl)
        self.writes.append(key)

    def delete(self, key):
        self.delete_attempts.append(key)
        if key in self.fail_delete_keys:
            if self.delete_before_error:
                super().delete(key)
            raise NovaCacheError("cache delete unavailable")
        return super().delete(key)

    def clear(self):
        if self.fail_clear:
            raise NovaCacheError("cache clear unavailable")
        super().clear()


class QueryStub:
    """Read fresh rows each time, with an optional invalidation during a fill."""

    def __init__(self, value=1, *, db="default", model="record", parameter=1, after_read=None):
        self.model = SimpleNamespace(
            _meta=SimpleNamespace(app_label="tests", model_name=model),
        )
        self.db = db
        self.query = SimpleNamespace(
            sql_with_params=lambda: (f"SELECT value FROM {model} WHERE id=%s", (parameter,))
        )
        self.rows = [value]
        self.after_read = after_read
        self.executions = 0
        self.error = None

    def __iter__(self):
        self.executions += 1
        if self.error is not None:
            raise self.error
        rows = list(self.rows)
        if self.after_read is not None:
            self.after_read()
        return iter(rows)


@pytest.fixture
def recovery_case():
    backend = FailingMemoryBackend()
    cache = QuerySetCache(backend=backend)
    query = QueryStub()
    assert cache.get_or_set(query) == [1]
    key = backend.writes[-1]
    query.rows = [2]
    return backend, cache, query, key


@pytest.mark.parametrize("reader", ["get", "get_or_set"])
@pytest.mark.parametrize("shared", [False, True], ids=["same-wrapper", "shared-state"])
def test_failed_delete_blocks_stale_reads(recovery_case, reader, shared, caplog):
    backend, cache, query, key = recovery_case
    backend.fail_delete_keys.add(key)
    assert cache.invalidate_model("tests.record") == 0
    assert backend.get(key) == [1], "The failure must really leave stale data behind"
    assert "Failed to delete cache key" in caplog.text
    target = QuerySetCache(_state=cache._state) if shared else cache
    if reader == "get":
        assert target.get(query) is None
        assert query.executions == 1
    else:
        assert target.get_or_set(query) == [2]
        assert query.executions == 2
        assert cache.get(query) == [2]


@pytest.mark.parametrize("deleted_before_error", [False, True])
def test_failed_key_remains_retryable_until_backend_confirms_absence(
    recovery_case, deleted_before_error
):
    backend, cache, query, key = recovery_case
    backend.fail_delete_keys.add(key)
    backend.delete_before_error = deleted_before_error
    assert cache.invalidate_model("tests.record") == 0
    backend.fail_delete_keys.clear()
    assert cache.invalidate_model("record") == (0 if deleted_before_error else 1)
    assert backend.delete_attempts.count(key) == 2
    assert cache.get(query) is None
    assert cache.invalidate_model("record") == 0
    assert backend.delete_attempts.count(key) == 2
    assert cache.get_or_set(query) == [2]


def test_partial_invalidation_retries_only_failed_keys(recovery_case):
    backend, cache, first, failed_key = recovery_case
    second = QueryStub(value=10, parameter=2)
    assert cache.get_or_set(second) == [10]
    successful_key = backend.writes[-1]
    backend.fail_delete_keys.add(failed_key)
    assert cache.invalidate_model("tests.record") == 1
    assert cache.get(first) is None
    assert cache.get(second) is None
    backend.fail_delete_keys.clear()
    assert cache.invalidate_model("tests.record") == 1
    assert backend.delete_attempts.count(failed_key) == 2
    assert backend.delete_attempts.count(successful_key) == 1


def test_failure_does_not_invalidate_other_model_or_database(recovery_case):
    backend, cache, query, key = recovery_case
    replica = QueryStub(value=10, db="replica")
    other_model = QueryStub(value=20, model="neighbor")
    cache.get_or_set(replica)
    cache.get_or_set(other_model)
    backend.fail_delete_keys.add(key)
    cache.invalidate_model("tests.record", db="default")
    assert cache.get(query) is None
    assert cache.get(replica) == [10]
    assert cache.get(other_model) == [20]
    backend.fail_delete_keys.clear()
    assert cache.invalidate_model("tests.record", db="*") == 2
    assert cache.get(other_model) == [20]


@pytest.mark.parametrize("reader", ["get", "get_or_set"])
@pytest.mark.parametrize("shared", [False, True], ids=["same-wrapper", "shared-state"])
def test_failed_clear_bypasses_cache(recovery_case, reader, shared, caplog):
    backend, cache, query, key = recovery_case
    backend.fail_clear = True
    cache.clear()
    assert backend.get(key) == [1]
    assert "Failed to clear cache backend" in caplog.text
    target = QuerySetCache(_state=cache._state) if shared else cache
    if reader == "get":
        assert target.get(query) is None
    else:
        assert target.get_or_set(query) == [2]
        query.rows = [3]
        assert target.get_or_set(query) == [3]
    assert backend.writes == [key], "A failed clear must not publish more entries"


def test_failed_clear_also_blocks_entries_absent_from_local_index(recovery_case):
    backend, cache, _, _ = recovery_case
    other_state = QuerySetCache(backend=backend)
    query = QueryStub(value=7, parameter=7)
    other_state.get_or_set(query)
    query.rows = [8]
    backend.fail_clear = True
    cache.clear()
    assert cache.get(query) is None
    assert cache.get_or_set(query) == [8]


def test_successful_clear_retry_restores_caching(recovery_case):
    backend, cache, query, _ = recovery_case
    backend.fail_clear = True
    cache.clear()
    cache.clear()
    assert cache.get_or_set(query) == [2]
    assert cache.get(query) is None
    backend.fail_clear = False
    cache.clear()
    assert cache.get(query) is None
    assert cache.get_or_set(query) == [2]
    query.rows = [3]
    assert cache.get(query) == [2], "Caching resumes after a successful clear"


def test_failed_clear_preserves_keys_for_explicit_invalidation(recovery_case):
    backend, cache, query, _ = recovery_case
    backend.fail_clear = True
    cache.clear()
    assert cache.invalidate_model("tests.record") == 1
    assert cache.get(query) is None


def test_failed_repair_write_keeps_stale_value_blocked(recovery_case):
    backend, cache, query, key = recovery_case
    backend.fail_delete_keys.add(key)
    cache.invalidate_model("tests.record")
    backend.fail_set = True
    with pytest.raises(NovaCacheError, match="cache write unavailable"):
        cache.get_or_set(query)
    assert backend.get(key) == [1]
    assert cache.get(query) is None
    backend.fail_set = False
    assert cache.get_or_set(query) == [2]
    assert cache.get(query) == [2]


@pytest.mark.parametrize("operation", ["invalidate", "clear"])
def test_database_error_propagates_while_invalid_cache_is_bypassed(recovery_case, operation):
    backend, cache, query, key = recovery_case
    if operation == "clear":
        backend.fail_clear = True
        cache.clear()
    else:
        backend.fail_delete_keys.add(key)
        cache.invalidate_model("tests.record")
    error = RuntimeError("database unavailable")
    query.error = error
    with pytest.raises(RuntimeError) as caught:
        cache.get_or_set(query)
    assert caught.value is error
    assert backend.writes == [key]


@pytest.mark.parametrize("operation", ["invalidate", "clear"])
def test_failed_invalidation_still_fences_an_overlapping_fill(recovery_case, operation):
    backend, cache, _, key = recovery_case
    if operation == "clear":
        backend.fail_clear = True
        invalidate = cache.clear
    else:
        backend.fail_delete_keys.add(key)

        def invalidate():
            return cache.invalidate_model("tests.record")

    overlapping = QueryStub(parameter=2, after_read=invalidate)
    assert cache.get_or_set(overlapping) == [1]
    assert backend.writes == [key]
    fresh = QueryStub(value=2, parameter=2)
    assert cache.get(fresh) is None
    assert cache.get_or_set(fresh) == [2]

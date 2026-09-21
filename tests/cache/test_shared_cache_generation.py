"""Shared-token contracts independent of transport or a running database."""

from threading import RLock

import pytest

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.generation import GenerationUnavailableError, generation_scope, new_generation
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_cache_failure_recovery import QueryStub


class SharedBackend(MemoryCacheBackend):
    """A shared server double; each QuerySetCache still has its own local index."""

    def __init__(self):
        super().__init__()
        self.tokens = {}
        self.token_lock = RLock()
        self.fail_read_generation = False
        self.fail_rotate = False
        self.fail_read = False
        self.fail_write = False
        self.before_write = None
        self.writes = []

    def get_generation(self, scope):
        if self.fail_read_generation:
            raise GenerationUnavailableError("metadata read failed")
        with self.token_lock:
            return self.tokens.setdefault(scope, new_generation())

    def rotate_generation(self, scope):
        if self.fail_rotate:
            raise GenerationUnavailableError("metadata write failed")
        with self.token_lock:
            self.tokens[scope] = new_generation()
            return self.tokens[scope]

    def get(self, key, default=None):
        if self.fail_read:
            raise OSError("data read failed")
        return super().get(key, default)

    def set(self, key, value, *, ttl=None):
        if self.fail_write:
            raise OSError("data write failed")
        if self.before_write is not None:
            callback, self.before_write = self.before_write, None
            callback()
        super().set(key, value, ttl=ttl)
        self.writes.append(key)


@pytest.fixture
def shared():
    backend = SharedBackend()
    return backend, QuerySetCache(backend=backend), QuerySetCache(backend=backend)


@pytest.mark.parametrize("name", ["record", "Record", "tests.record", "tests.Record"])
def test_writer_with_empty_index_rotates_shared_generation(shared, name):
    backend, reader, writer = shared
    assert reader.get_or_set(QueryStub()) == [1]
    old = backend.writes[-1]
    assert writer.invalidate_model(name) == 0  # Physical deletion count remains local.
    assert backend.get(old) == [1]
    assert reader.get(QueryStub()) is None
    assert reader.get_or_set(QueryStub(2)) == [2]


@pytest.mark.parametrize("alias", ["default", "replica", "*"])
def test_database_scopes_and_wildcard(shared, alias):
    _, reader, writer = shared
    for db in ("default", "replica"):
        reader.get_or_set(QueryStub(db=db))
    reader.get_or_set(QueryStub(model="neighbor"))
    writer.invalidate_model("tests.record", db=alias)
    for db in ("default", "replica"):
        assert reader.get(QueryStub(db=db)) == (None if alias in {db, "*"} else [1])
    assert reader.get(QueryStub(model="neighbor")) == [1]


@pytest.mark.parametrize("phase", ["after-sql", "inside-set"])
def test_late_fill_cannot_publish_into_new_generation(shared, phase):
    backend, reader, writer = shared

    def invalidate():
        writer.invalidate_model("tests.record")

    query = QueryStub(after_read=invalidate if phase == "after-sql" else None)
    old = reader._generate_key(query)[0]
    if phase == "inside-set":
        backend.before_write = invalidate
    assert reader.get_or_set(query) == [1]  # This request overlapped the write.
    assert backend.get(old) == ([1] if phase == "inside-set" else None)
    assert reader.get(QueryStub()) is None
    assert reader.get_or_set(QueryStub(2)) == [2]


@pytest.mark.parametrize("scope_db", ["default", "*"])
def test_metadata_eviction_never_reuses_old_result_keys(shared, scope_db):
    backend, reader, _ = shared
    reader.get_or_set(QueryStub())
    old = backend.writes[-1]
    scope = generation_scope("record", scope_db)
    old_token = backend.tokens.pop(scope)
    assert reader.get(QueryStub()) is None
    assert backend.tokens[scope] != old_token
    assert backend.get(old) == [1]
    assert reader.get_or_set(QueryStub(2)) == [2]


def test_eviction_during_late_set_does_not_restore_old_namespace(shared):
    backend, reader, _ = shared
    old = reader._generate_key(QueryStub())[0]
    backend.before_write = backend.tokens.clear
    assert reader.get_or_set(QueryStub()) == [1]
    assert backend.get(old) == [1]
    assert reader.get(QueryStub()) is None
    assert reader.get_or_set(QueryStub(2)) == [2]


@pytest.mark.parametrize("phase", ["before-query", "after-sql"])
def test_unreadable_generation_bypasses_cache_and_does_not_publish(shared, phase, caplog):
    backend, reader, _ = shared

    def fail():
        backend.fail_read_generation = True

    if phase == "before-query":
        fail()
    query = QueryStub(2, after_read=fail if phase == "after-sql" else None)
    assert reader.get_or_set(query) == [2]
    assert query.executions == 1
    assert backend.writes == []
    assert reader.get(QueryStub()) is None
    assert "bypassing cache" in caplog.text


def test_failed_rotation_is_pending_and_retried_before_local_read(shared, caplog):
    backend, reader, writer = shared
    reader.get_or_set(QueryStub())
    backend.fail_rotate = True
    assert writer.invalidate_model("tests.record") == 0
    assert writer.get(QueryStub()) is None
    assert writer.get_or_set(QueryStub(2)) == [2]
    assert len(backend.writes) == 1
    assert "remote readers may retain stale data" in caplog.text
    backend.fail_rotate = False
    assert writer.get(QueryStub()) is None  # Retries rotation before reading.
    assert reader.get(QueryStub()) is None
    assert reader.get_or_set(QueryStub(2)) == [2]


def test_rotation_failure_does_not_falsely_claim_remote_notification(shared):
    backend, reader, writer = shared
    reader.get_or_set(QueryStub())
    backend.fail_rotate = True
    writer.invalidate_model("tests.record")
    # This limitation requires durable coordination (e.g. an outbox) to remove:
    # a partitioned writer cannot notify a reader which still reaches the server.
    assert reader.get(QueryStub()) == [1]
    assert writer._state.pending_generations


@pytest.mark.parametrize("failure", ["fail_read", "fail_write"])
def test_remote_data_failure_returns_database_result(shared, failure, caplog):
    backend, reader, _ = shared
    setattr(backend, failure, True)
    query = QueryStub(2)
    assert reader.get_or_set(query) == [2]
    assert query.executions == 1
    assert "Shared cache" in caplog.text


def test_database_error_still_propagates_when_generation_is_unavailable(shared):
    backend, reader, _ = shared
    backend.fail_read_generation = True
    query = QueryStub()
    error = RuntimeError("database unavailable")
    query.error = error
    with pytest.raises(RuntimeError) as raised:
        reader.get_or_set(query)
    assert raised.value is error


def test_corrupt_generation_is_bypassed_until_explicit_rotation(shared):
    backend, reader, writer = shared
    reader.get_or_set(QueryStub())
    scope = generation_scope("record", "default")
    backend.tokens[scope] = "invalid-token"
    assert reader.get(QueryStub()) is None
    assert reader.get_or_set(QueryStub(2)) == [2]
    assert len(backend.writes) == 1
    writer.invalidate_model("tests.record")
    assert reader.get_or_set(QueryStub(2)) == [2]

"""Write ownership is independent of read ownership and generation fencing."""

import copy
from asyncio import CancelledError
from unittest.mock import Mock

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.backends.serializers import PickleSerializer
from nova.cache.queryset_cache import QuerySetCache
from nova.cache.write_contract import DetachedWriteBackend
from tests.cache.test_queryset_cache_failure_recovery import QueryStub
from tests.cache.test_queryset_detached_reads import native_backend as native_backend
from tests.cache.test_queryset_detached_reads import snapshot_spy


@pytest.mark.parametrize("empty", [False, True])
def test_native_fill_serializes_original_result_once(native_backend, monkeypatch, empty):
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub({"items": [1]})
    if empty:
        query.rows = []
    expected = copy.deepcopy(query.rows)
    snapshot = snapshot_spy(monkeypatch)
    serializer = native_backend._serializer
    dumps, loads = Mock(wraps=serializer.dumps), Mock(wraps=serializer.loads)
    monkeypatch.setattr(serializer, "dumps", dumps)
    monkeypatch.setattr(serializer, "loads", loads)
    assert isinstance(native_backend, DetachedWriteBackend)
    assert native_backend.stores_detached_values is True
    rows = cache.get_or_set(query)
    serialized = dumps.call_args.args[0]
    if isinstance(native_backend, MemcachedCacheBackend):
        serialized = serialized[2]  # Memcached's versioned TTL envelope.
    assert serialized is rows
    dumps.assert_called_once()
    loads.assert_not_called()
    snapshot.assert_not_called()
    if rows:
        rows[0]["items"].append(2)
    rows.append("caller-only")
    assert cache.get_or_set(query) == expected
    assert query.executions == 1
    dumps.assert_called_once()
    loads.assert_called_once()
    snapshot.assert_not_called()


@pytest.mark.parametrize("flag", [None, False, 1, "true"])
def test_non_true_write_declaration_keeps_snapshot(monkeypatch, flag):
    class UncertainBackend(MemoryCacheBackend):
        stores_detached_values = flag

    backend = UncertainBackend()
    cache = QuerySetCache(backend=backend)
    query = QueryStub({"items": [1]})
    spy = snapshot_spy(monkeypatch)
    rows = cache.get_or_set(query)
    spy.assert_called_once()
    rows[0]["items"].append(2)
    rows.clear()
    assert backend.get(cache._generate_key(query)[0]) == [{"items": [1]}]


@pytest.mark.parametrize("kind", ["memory", "redis-name", "read-guarantee"])
def test_missing_write_guarantee_never_borrows_input(monkeypatch, kind):
    class NamedBackend(MemoryCacheBackend):
        backend_name = "redis"

    class DetachedReads(MemoryCacheBackend):
        returns_detached_values = True

        def get(self, key, default=None):
            return copy.deepcopy(super().get(key, default))

    backend = {
        "memory": MemoryCacheBackend,
        "redis-name": NamedBackend,
        "read-guarantee": DetachedReads,
    }[kind]()
    cache = QuerySetCache(backend=backend)
    query = QueryStub({"items": [1]})
    spy = snapshot_spy(monkeypatch)
    rows = cache.get_or_set(query)
    spy.assert_called_once()
    rows[0]["items"].append(2)
    rows.clear()
    assert backend.get(cache._generate_key(query)[0]) == [{"items": [1]}]


def test_explicit_write_guarantee_does_not_opt_in_to_detached_reads(monkeypatch):
    class CopyOnWrite(MemoryCacheBackend):
        stores_detached_values = True

        def set(self, key, value, *, ttl=None):
            return super().set(key, copy.deepcopy(value), ttl=ttl)

    cache = QuerySetCache(backend=CopyOnWrite())
    spy = snapshot_spy(monkeypatch)
    query = QueryStub({"items": [1]})
    rows = cache.get_or_set(query)
    spy.assert_not_called()
    rows[0]["items"].append(2)
    first = cache.get(query)
    assert first == [{"items": [1]}]
    first[0]["items"].append(3)
    assert cache.get(query) == [{"items": [1]}]
    assert spy.call_count == 2


def test_replaced_serializer_receives_a_snapshot(native_backend, monkeypatch):
    class RetainingSerializer(PickleSerializer):
        def dumps(self, value):
            self.retained = value
            return super().dumps(value)

    native_backend._serializer = serializer = RetainingSerializer()
    assert native_backend.stores_detached_values is False
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub({"items": [1]})
    spy = snapshot_spy(monkeypatch)
    rows = cache.get_or_set(query)
    spy.assert_called_once()
    retained = serializer.retained
    if isinstance(native_backend, MemcachedCacheBackend):
        retained = retained[2]
    assert retained is not rows
    rows[0]["items"].append(2)
    assert retained == [{"items": [1]}]
    assert cache.get(query) == [{"items": [1]}]
    native_backend._serializer = PickleSerializer()
    assert native_backend.stores_detached_values is True


def test_backend_subclass_does_not_inherit_write_opt_in(native_backend, monkeypatch):
    base = type(native_backend)

    class RetainingBackend(base):
        def set(self, key, value, *, ttl=None):
            self.retained = value
            return super().set(key, value, ttl=ttl)

    kwargs = {"key_prefix": ""} if base is RedisCacheBackend else {}
    backend = RetainingBackend(
        client=native_backend._client, generation_writer=native_backend._generation_writer, **kwargs
    )
    assert backend.stores_detached_values is False
    spy = snapshot_spy(monkeypatch)
    cache = QuerySetCache(backend=backend)
    rows = cache.get_or_set(QueryStub({"items": [1]}))
    spy.assert_called_once()
    rows[0]["items"].append(2)
    assert backend.retained == [{"items": [1]}]


@pytest.mark.parametrize("forward_write", [False, True])
def test_wrapper_must_forward_write_guarantee_explicitly(
    native_backend, monkeypatch, forward_write
):
    class ReadWrapper:
        returns_detached_values = True

        def get(self, key):
            return native_backend.get(key)

        def set(self, key, value, *, ttl=None):
            return native_backend.set(key, value, ttl=ttl)

    class WriteWrapper(ReadWrapper):
        @property
        def stores_detached_values(self):
            return native_backend.stores_detached_values

    cache = QuerySetCache(backend=WriteWrapper() if forward_write else ReadWrapper())
    spy = snapshot_spy(monkeypatch)
    query = QueryStub({"items": [1]})
    rows = cache.get_or_set(query)
    assert spy.call_count == (0 if forward_write else 1)
    rows[0]["items"].append(2)
    assert cache.get(query) == [{"items": [1]}]


def test_broken_write_property_falls_back_to_snapshot(monkeypatch, caplog):
    class BrokenDeclaration(MemoryCacheBackend):
        @property
        def stores_detached_values(self):
            raise RuntimeError("cannot determine ownership")

    cache = QuerySetCache(backend=BrokenDeclaration())
    spy = snapshot_spy(monkeypatch)
    query = QueryStub({"items": [1]})
    rows = cache.get_or_set(query)
    spy.assert_called_once()
    rows[0]["items"].append(2)
    assert cache.get(query) == [{"items": [1]}]
    assert "write ownership unavailable" in caplog.text


class Unserializable:
    def __reduce__(self):
        raise TypeError("unsupported result")


def test_fast_serialization_failure_returns_original_rows_and_can_recover(
    native_backend, monkeypatch, caplog
):
    cache = QuerySetCache(backend=native_backend)
    value = Unserializable()
    query = QueryStub(value)
    spy = snapshot_spy(monkeypatch)
    rows = cache.get_or_set(query)
    assert rows[0] is value
    assert not cache._state.model_keys
    assert cache.get(query) is None
    assert "Shared cache write failed" in caplog.text
    spy.assert_not_called()
    query.rows = [{"items": [1]}]
    assert cache.get_or_set(query) == [{"items": [1]}]
    assert cache.get(query) == [{"items": [1]}]
    spy.assert_not_called()


@pytest.mark.parametrize("applied", [False, True])
def test_transport_failure_preserves_rows_before_or_after_apply(
    native_backend, monkeypatch, applied
):
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub({"items": [1]})
    key = cache._generate_key(query)[0]
    original = native_backend._client.set

    def failing_set(candidate, value, **kwargs):
        if candidate != key:
            return original(candidate, value, **kwargs)
        if applied:
            original(candidate, value, **kwargs)
        raise OSError("reply unavailable")

    monkeypatch.setattr(native_backend._client, "set", failing_set)
    spy = snapshot_spy(monkeypatch)
    rows = cache.get_or_set(query)
    assert rows == [{"items": [1]}]
    assert not cache._state.model_keys
    rows[0]["items"].append(2)
    assert native_backend.get(key) == ([{"items": [1]}] if applied else None)
    spy.assert_not_called()
    monkeypatch.setattr(native_backend._client, "set", original)
    query.rows = [{"items": [3]}]
    assert cache.get_or_set(query) == ([{"items": [1]}] if applied else [{"items": [3]}])


def test_non_generation_backend_keeps_existing_write_error_policy():
    class Unavailable(MemoryCacheBackend):
        stores_detached_values = True

        def set(self, key, value, *, ttl=None):
            raise OSError("storage unavailable")

    with pytest.raises(OSError, match="storage unavailable"):
        QuerySetCache(backend=Unavailable()).get_or_set(QueryStub())


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, CancelledError])
@pytest.mark.parametrize("stage", ["property", "serializer", "transport"])
def test_interruption_is_not_swallowed(native_backend, monkeypatch, error_type, stage):
    error = error_type()

    def interrupt(*args, **kwargs):
        raise error

    cache = QuerySetCache(backend=native_backend)
    query = QueryStub()
    cache._generate_key(query)  # Metadata writes are outside the fault injection.
    if stage == "property":
        monkeypatch.setattr(type(native_backend), "stores_detached_values", property(interrupt))
    elif stage == "serializer":
        monkeypatch.setattr(native_backend._serializer, "dumps", interrupt)
    else:
        monkeypatch.setattr(native_backend._client, "set", interrupt)
    with pytest.raises(error_type) as caught:
        cache.get_or_set(query)
    assert caught.value is error
    assert not cache._state.model_keys


def test_rotation_during_query_still_prevents_publication(native_backend, monkeypatch):
    reader, writer = QuerySetCache(backend=native_backend), QuerySetCache(backend=native_backend)
    query = QueryStub(after_read=lambda: writer.invalidate_model("record"))
    key = reader._generate_key(query)[0]
    dumps = Mock(wraps=native_backend._serializer.dumps)
    monkeypatch.setattr(native_backend._serializer, "dumps", dumps)
    assert reader.get_or_set(query) == [1]
    assert native_backend.get(key) is None
    dumps.assert_not_called()


@pytest.mark.parametrize("stage", ["serializer", "transport"])
def test_rotation_after_last_check_cannot_republish_into_new_generation(
    native_backend, monkeypatch, stage
):
    reader, writer = QuerySetCache(backend=native_backend), QuerySetCache(backend=native_backend)
    query = QueryStub()
    old_key = reader._generate_key(query)[0]
    called = False

    def rotate_once():
        nonlocal called
        if not called:
            called = True
            writer.invalidate_model("record")

    if stage == "serializer":
        original = native_backend._serializer.dumps

        def rotating_dumps(value):
            rotate_once()
            return original(value)

        monkeypatch.setattr(native_backend._serializer, "dumps", rotating_dumps)
    else:
        original = native_backend._client.set

        def rotating_set(key, value, **kwargs):
            if key == old_key:
                rotate_once()
            return original(key, value, **kwargs)

        monkeypatch.setattr(native_backend._client, "set", rotating_set)
    assert reader.get_or_set(query) == [1]
    assert called
    assert native_backend.get(old_key) == [1], "Stale bytes must really exist as a control"
    assert reader._generate_key(query)[0] != old_key
    fresh = QueryStub(2)
    assert reader.get(fresh) is None
    assert reader.get_or_set(fresh) == [2]

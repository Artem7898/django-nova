"""Skip read copies only under an explicit, verified ownership contract."""

import copy
from unittest.mock import Mock

import pytest

from nova.cache import queryset_cache as cache_module
from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.backends.serializers import PickleSerializer
from nova.cache.generation_transport import MemcachedGenerationWriter
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_generation_command_replay import StoreClient
from tests.cache.test_queryset_cache_failure_recovery import QueryStub


@pytest.fixture(params=["redis", "memcached"])
def native_backend(request):
    client = StoreClient()
    writer = MemcachedGenerationWriter(client)
    if request.param == "redis":
        return RedisCacheBackend(client=client, key_prefix="", generation_writer=writer)
    return MemcachedCacheBackend(client=client, generation_writer=writer)


def snapshot_spy(monkeypatch):
    spy = Mock(wraps=cache_module.snapshot_rows)
    monkeypatch.setattr(cache_module, "snapshot_rows", spy)
    return spy


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_native_hit_deserializes_once_without_another_snapshot(native_backend, monkeypatch, entry):
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub({"items": [1]})
    cache.get_or_set(query)
    spy = snapshot_spy(monkeypatch)
    loads = Mock(wraps=native_backend._serializer.loads)
    dumps = Mock(wraps=native_backend._serializer.dumps)
    monkeypatch.setattr(native_backend._serializer, "loads", loads)
    monkeypatch.setattr(native_backend._serializer, "dumps", dumps)
    returned = getattr(cache, entry)(query)
    assert returned == [{"items": [1]}]
    returned[0]["items"].append(2)
    assert getattr(cache, entry)(query) == [{"items": [1]}]
    assert query.executions == 1
    assert loads.call_count == 2  # Exactly one deserialization per successful read.
    dumps.assert_not_called()
    spy.assert_not_called()


def test_native_fill_serializes_once_and_preserves_read_isolation(native_backend, monkeypatch):
    spy = snapshot_spy(monkeypatch)
    dumps = Mock(wraps=native_backend._serializer.dumps)
    monkeypatch.setattr(native_backend._serializer, "dumps", dumps)
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub({"items": [1]})
    rows = cache.get_or_set(query)
    spy.assert_not_called()
    assert dumps.call_count == 1
    rows[0]["items"].append(2)
    assert cache.get(query) == [{"items": [1]}]
    spy.assert_not_called()
    assert dumps.call_count == 1


class MemoizingSerializer(PickleSerializer):
    """A serializer can retain mutable objects even when it uses pickle."""

    def __init__(self):
        self.decoded = {}

    def loads(self, payload):
        if payload not in self.decoded:
            self.decoded[payload] = super().loads(payload)
        return self.decoded[payload]


def test_replaced_serializer_keeps_defensive_copy(native_backend, monkeypatch):
    native_backend._serializer = MemoizingSerializer()
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub({"items": [1]})
    cache.get_or_set(query)
    spy = snapshot_spy(monkeypatch)
    first = cache.get(query)
    first[0]["items"].append(2)
    assert cache.get_or_set(query) == [{"items": [1]}]
    assert spy.call_count == 2
    assert query.executions == 1


def test_backend_subclass_with_memoized_reads_does_not_inherit_opt_in(native_backend, monkeypatch):
    base = type(native_backend)

    class MemoizingBackend(base):
        def get(self, key, default=None):
            if not hasattr(self, "remembered"):
                self.remembered = {}
            if key not in self.remembered:
                self.remembered[key] = super().get(key, default)
            return self.remembered[key]

    client = native_backend._client
    kwargs = {"key_prefix": ""} if base is RedisCacheBackend else {}
    backend = MemoizingBackend(
        client=client, generation_writer=MemcachedGenerationWriter(client), **kwargs
    )
    cache = QuerySetCache(backend=backend)
    query = QueryStub({"items": [1]})
    key = cache._generate_key(query)[0]
    backend.set(key, query.rows)
    spy = snapshot_spy(monkeypatch)
    returned = cache.get(query)
    returned[0]["items"].append(2)
    assert cache.get(query) == [{"items": [1]}]
    assert spy.call_count == 2
    assert query.executions == 0


@pytest.mark.parametrize("flag", [False, None, 1, "true"])
def test_non_true_declaration_preserves_nested_isolation(monkeypatch, flag):
    class UncertainBackend(MemoryCacheBackend):
        returns_detached_values = flag

    backend = UncertainBackend()
    cache = QuerySetCache(backend=backend)
    query = QueryStub({"items": [1]})
    cache.get_or_set(query)
    spy = snapshot_spy(monkeypatch)
    first = cache.get(query)
    first[0]["items"].append(2)
    assert cache.get_or_set(query) == [{"items": [1]}]
    assert spy.call_count == 2
    assert query.executions == 1


def test_missing_declaration_and_backend_name_do_not_opt_in(monkeypatch):
    class NamedBackend(MemoryCacheBackend):
        @property
        def backend_name(self):
            return "redis"

    cache = QuerySetCache(backend=NamedBackend())
    query = QueryStub({"items": [1]})
    cache.get_or_set(query)
    spy = snapshot_spy(monkeypatch)
    cache.get(query)[0]["items"].append(2)
    assert cache.get(query) == [{"items": [1]}]
    assert spy.call_count == 2


def test_custom_backend_can_explicitly_guarantee_detached_reads(monkeypatch):
    class CopyingBackend(MemoryCacheBackend):
        returns_detached_values = True

        def get(self, key, default=None):
            return copy.deepcopy(super().get(key, default))

    cache = QuerySetCache(backend=CopyingBackend())
    query = QueryStub({"items": [1]})
    cache.get_or_set(query)
    spy = snapshot_spy(monkeypatch)
    first = cache.get(query)
    first[0]["items"].append(2)
    assert cache.get(query) == [{"items": [1]}]
    spy.assert_not_called()


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
@pytest.mark.parametrize("value", [42, "text", {"items": []}, (1, 2)])
def test_detached_but_invalid_result_shape_is_still_a_miss(native_backend, entry, value):
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub(2)
    key = cache._generate_key(query)[0]
    native_backend.set(key, value)
    assert getattr(cache, entry)(query) == (None if entry == "get" else [2])
    assert query.executions == (0 if entry == "get" else 1)


def test_empty_list_remains_an_isolated_hit(native_backend, monkeypatch):
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub()
    query.rows = []
    cache.get_or_set(query)
    spy = snapshot_spy(monkeypatch)
    cache.get(query).append("caller-only")
    assert cache.get_or_set(query) == []
    assert query.executions == 1
    spy.assert_not_called()


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_corrupt_serialized_data_still_misses(native_backend, entry):
    cache = QuerySetCache(backend=native_backend)
    query = QueryStub(2)
    key = cache._generate_key(query)[0]
    native_backend._client.set(key, b"not-a-pickle")
    assert getattr(cache, entry)(query) == (None if entry == "get" else [2])
    assert query.executions == (0 if entry == "get" else 1)


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_rotation_after_decoding_still_rejects_the_hit(native_backend, monkeypatch, entry):
    reader = QuerySetCache(backend=native_backend)
    writer = QuerySetCache(backend=native_backend)
    reader.get_or_set(QueryStub())
    original_get = native_backend.get
    called = False

    def invalidate_after_decode(key):
        nonlocal called
        value = original_get(key)
        if not called:
            called = True
            writer.invalidate_model("record")
        return value

    monkeypatch.setattr(native_backend, "get", invalidate_after_decode)
    query = QueryStub(2)
    assert getattr(reader, entry)(query) == (None if entry == "get" else [2])
    assert query.executions == (0 if entry == "get" else 1)


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_broken_ownership_property_cannot_leak_a_shared_result(monkeypatch, entry):
    class BrokenBackend(MemoryCacheBackend):
        @property
        def returns_detached_values(self):
            raise RuntimeError("ownership is unknown")

    cache = QuerySetCache(backend=BrokenBackend())
    query = QueryStub(2)
    key = cache._generate_key(query)[0]
    cache._state.backend.set(key, [1])
    assert getattr(cache, entry)(query) == (None if entry == "get" else [2])
    assert query.executions == (0 if entry == "get" else 1)


def test_base_exception_in_ownership_property_is_not_swallowed():
    class InterruptedBackend(MemoryCacheBackend):
        @property
        def returns_detached_values(self):
            raise KeyboardInterrupt

    cache = QuerySetCache(backend=InterruptedBackend())
    query = QueryStub()
    cache.get_or_set(query)
    with pytest.raises(KeyboardInterrupt):
        cache.get(query)

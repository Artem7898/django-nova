"""ORM results and commit invalidation over real, independently connected caches.

Opt in with NOVA_TEST_REDIS_URL / NOVA_TEST_MEMCACHED_SERVER. A configured
but unreachable service fails the test. Cleanup deletes only this test's keys.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from django.db import transaction
from django.db.models.signals import post_delete, post_save

from nova.cache import invalidation
from nova.cache.generation import GenerationUnavailableError, generation_key, generation_scope
from nova.cache.queryset_cache import QuerySetCache
from tests.models import CachedItem

pytestmark = pytest.mark.integration


class TrackedBackend:
    """Delegate real I/O and remember exact keys for scoped cleanup."""

    def __init__(self, backend, keys):
        self.backend = backend
        self.keys = keys

    def get(self, key):
        self.keys.add(key)
        return self.backend.get(key)

    @property
    def returns_detached_values(self):
        return self.backend.returns_detached_values

    @property
    def stores_detached_values(self):
        return self.backend.stores_detached_values

    def set(self, key, value, *, ttl=None):
        self.keys.add(key)
        return self.backend.set(key, value, ttl=ttl)

    def delete(self, key):
        return self.backend.delete(key)

    def get_generation(self, scope):
        self.keys.add(generation_key(scope))
        return self.backend.get_generation(scope)

    def get_generations(self, scopes):
        self.keys.update(generation_key(scope) for scope in scopes)
        return self.backend.get_generations(scopes)

    def rotate_generation(self, scope):
        self.keys.add(generation_key(scope))
        return self.backend.rotate_generation(scope)

    def clear(self):
        raise AssertionError("Integration tests must not flush a shared server")


@pytest.fixture(params=["redis", "memcached"])
def remote_cache(request, transactional_db):
    variable = "NOVA_TEST_REDIS_URL" if request.param == "redis" else "NOVA_TEST_MEMCACHED_SERVER"
    address = os.environ.get(variable)
    if not address:
        pytest.skip(f"Set {variable} to run real {request.param} integration tests")

    namespace = f"nova-qs-it:{uuid4().hex}"
    keys = set()
    backends = []
    with ExitStack() as resources:

        def new_cache():
            if request.param == "redis":
                from redis import Redis

                from nova.cache.backends.redis import RedisCacheBackend

                client = Redis.from_url(
                    address, decode_responses=False, socket_connect_timeout=3, socket_timeout=3
                )
                resources.callback(client.close)
                assert client.ping()
                backend = RedisCacheBackend(client=client, key_prefix=namespace)
            else:
                from pymemcache.client.base import Client

                from nova.cache.backends.memcached import MemcachedCacheBackend

                endpoint = urlsplit(f"//{address}")
                assert endpoint.hostname and endpoint.port, "Expected host:port or [IPv6]:port"
                client = Client(
                    (endpoint.hostname, endpoint.port),
                    key_prefix=f"{namespace}:".encode("ascii"),
                    default_noreply=False,
                    connect_timeout=3,
                    timeout=3,
                )
                resources.callback(client.close)
                assert client.version()
                backend = MemcachedCacheBackend(client=client)
            backends.append(backend)
            return QuerySetCache(backend=TrackedBackend(backend, keys), ttl=60)

        cache = new_cache()
        try:
            yield SimpleNamespace(cache=cache, new_cache=new_cache)
        finally:
            for key in keys:
                backends[0].delete(key)


@pytest.fixture
def signal_cache(remote_cache, monkeypatch):
    receivers = []
    for signal in (post_save, post_delete):
        original_connect = signal.connect

        def tracked_connect(receiver, *args, _signal=signal, _connect=original_connect, **kwargs):
            receivers.append((_signal, receiver, kwargs.get("sender")))
            return _connect(receiver, *args, **kwargs)

        monkeypatch.setattr(signal, "connect", tracked_connect)

    cache = remote_cache.cache
    invalidation.connect_invalidation(CachedItem, cache=cache)
    try:
        yield cache
    finally:
        for signal, receiver, sender in receivers:
            signal.disconnect(receiver, sender=sender)
        invalidation._CONNECTED_SIGNALS.discard((CachedItem, id(cache)))


def test_models_roundtrip_through_an_independent_client(remote_cache, django_assert_num_queries):
    row = CachedItem.objects.create(name="Nova", value=1)
    cache = remote_cache.cache
    with django_assert_num_queries(1):
        first = cache.get_or_set(CachedItem.objects.filter(pk=row.pk))
    second_cache = remote_cache.new_cache()
    with django_assert_num_queries(0):
        second = second_cache.get_or_set(CachedItem.objects.filter(pk=row.pk))
        third = cache.get(CachedItem.objects.filter(pk=row.pk))
    assert isinstance(second[0], CachedItem)
    assert (second[0].pk, second[0].name, second[0].value) == (row.pk, "Nova", 1)
    assert third[0].pk == row.pk
    assert first[0] is not second[0]
    assert not second[0]._state.adding
    assert second[0]._state.db == row._state.db


def test_long_unicode_query_parameters(remote_cache, django_assert_num_queries):
    row = CachedItem.objects.create(name="缓存\n\t🙂", value=1)
    names = [row.name, "x" * 10_000]
    cache = remote_cache.cache
    assert [item.pk for item in cache.get_or_set(CachedItem.objects.filter(name__in=names))] == [
        row.pk
    ]
    with django_assert_num_queries(0):
        assert [
            item.pk for item in cache.get_or_set(CachedItem.objects.filter(name__in=names))
        ] == [row.pk]


def test_distinct_parameters_do_not_share_results(remote_cache, django_assert_num_queries):
    CachedItem.objects.create(name="first", value=1)
    CachedItem.objects.create(name="second", value=2)
    cache = remote_cache.cache
    for value in (1, 2):
        with django_assert_num_queries(1):
            assert [
                item.value for item in cache.get_or_set(CachedItem.objects.filter(value=value))
            ] == [value]
    with django_assert_num_queries(0):
        for value in (1, 2):
            assert [
                item.value for item in cache.get_or_set(CachedItem.objects.filter(value=value))
            ] == [value]


def test_ordering_and_slicing_are_part_of_query_identity(remote_cache, django_assert_num_queries):
    for value in (1, 2, 3):
        CachedItem.objects.create(name=f"item-{value}", value=value)
    cache = remote_cache.cache
    for ordering, limit, expected in [
        ("value", 2, [1, 2]),
        ("-value", 2, [3, 2]),
        ("value", 1, [1]),
    ]:
        with django_assert_num_queries(1):
            result = cache.get_or_set(CachedItem.objects.order_by(ordering)[:limit])
        assert [item.value for item in result] == expected
        with django_assert_num_queries(0):
            again = cache.get_or_set(CachedItem.objects.order_by(ordering)[:limit])
        assert [item.value for item in again] == expected


def test_result_shapes_remain_distinct_after_serialization(remote_cache, django_assert_num_queries):
    CachedItem.objects.create(name="Nova", value=7)
    cache = remote_cache.cache

    def queries():
        base = CachedItem.objects.order_by("pk")
        return (
            base.values("value"),
            base.values_list("value"),
            base.values_list("value", flat=True),
            base.values_list("value", named=True),
        )

    with django_assert_num_queries(4):
        results = [cache.get_or_set(query) for query in queries()]
    with django_assert_num_queries(0):
        restored = [cache.get_or_set(query) for query in queries()]
    for values in (results, restored):
        assert values[:3] == [[{"value": 7}], [(7,)], [7]]
        assert type(values[1][0]) is tuple
        assert values[3][0].value == 7
        assert values[3][0]._fields == ("value",)


@pytest.mark.parametrize("empty_filter", [False, True], ids=["none", "empty-in"])
def test_statically_empty_queries(remote_cache, django_assert_num_queries, empty_filter):
    def query():
        return CachedItem.objects.filter(pk__in=[]) if empty_filter else CachedItem.objects.none()

    with django_assert_num_queries(0):
        assert remote_cache.cache.get_or_set(query()) == []
        assert remote_cache.new_cache().get(query()) == []


def test_empty_database_result_is_a_cache_hit(remote_cache, django_assert_num_queries):
    cache = remote_cache.cache
    with django_assert_num_queries(1):
        assert cache.get_or_set(CachedItem.objects.filter(name="absent")) == []
    with django_assert_num_queries(0):
        assert cache.get_or_set(CachedItem.objects.filter(name="absent")) == []


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_committed_write_invalidates_remote_result(
    signal_cache, operation, django_assert_num_queries
):
    row = CachedItem.objects.create(name="Nova", value=1)
    pk = row.pk
    assert [item.value for item in signal_cache.get_or_set(CachedItem.objects.filter(pk=pk))] == [1]
    old_key = signal_cache._generate_key(CachedItem.objects.filter(pk=pk))[0]
    backend = signal_cache._state.backend
    with transaction.atomic():
        if operation == "save":
            row.value = 2
            row.save(update_fields=["value"])
        else:
            row.delete()
        with django_assert_num_queries(0):
            assert signal_cache.get(CachedItem.objects.filter(pk=pk)) is None
            # The committed entry survives until on_commit; the writer's
            # transactional lookup must not return it or overwrite it.
            assert backend.get(old_key)[0].value == 1
        with django_assert_num_queries(1):
            own_rows = signal_cache.get_or_set(CachedItem.objects.filter(pk=pk))
        assert [item.value for item in own_rows] == ([2] if operation == "save" else [])
        assert backend.get(old_key)[0].value == 1
    assert signal_cache.get(CachedItem.objects.filter(pk=pk)) is None
    with django_assert_num_queries(1):
        fresh = signal_cache.get_or_set(CachedItem.objects.filter(pk=pk))
    assert [item.value for item in fresh] == ([2] if operation == "save" else [])


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_rollback_preserves_remote_result(signal_cache, operation, django_assert_num_queries):
    row = CachedItem.objects.create(name="Nova", value=1)
    pk = row.pk
    signal_cache.get_or_set(CachedItem.objects.filter(pk=pk))
    with transaction.atomic():
        if operation == "save":
            row.value = 2
            row.save(update_fields=["value"])
        else:
            row.delete()
        transaction.set_rollback(True)
    assert CachedItem.objects.get(pk=pk).value == 1
    with django_assert_num_queries(0):
        assert [
            item.value for item in signal_cache.get_or_set(CachedItem.objects.filter(pk=pk))
        ] == [1]


@pytest.mark.parametrize("method", ["get", "get_or_set"])
def test_reader_can_invalidate_a_remote_hit(remote_cache, method, django_assert_num_queries):
    row = CachedItem.objects.create(name="Nova", value=1)
    writer = remote_cache.cache
    writer.get_or_set(CachedItem.objects.filter(pk=row.pk))
    reader = remote_cache.new_cache()
    with django_assert_num_queries(0):
        result = getattr(reader, method)(CachedItem.objects.filter(pk=row.pk))
    assert result[0].value == 1
    assert reader.invalidate_model(CachedItem._meta.label_lower) == 1
    assert writer.get(CachedItem.objects.filter(pk=row.pk)) is None


@pytest.mark.parametrize("scope_db", ["default", "*"])
def test_real_metadata_eviction_hides_surviving_results(remote_cache, scope_db):
    row = CachedItem.objects.create(name="Nova", value=1)
    cache = remote_cache.cache
    backend = cache._state.backend.backend
    query = CachedItem.objects.filter(pk=row.pk)
    cache.get_or_set(query)
    old = cache._generate_key(query)[0]
    assert backend.delete(generation_key(generation_scope("cacheditem", scope_db)))
    assert backend.get(old)[0].value == 1
    assert cache.get(CachedItem.objects.filter(pk=row.pk)) is None
    assert cache._generate_key(query)[0] != old


def test_real_corrupt_metadata_bypasses_stale_data(remote_cache, django_assert_num_queries):
    row = CachedItem.objects.create(name="Nova", value=1)
    cache = remote_cache.cache
    backend = cache._state.backend.backend
    query = CachedItem.objects.filter(pk=row.pk)
    cache.get_or_set(query)
    old = cache._generate_key(query)[0]
    metadata_key = generation_key(generation_scope("cacheditem", "default"))
    raw_key = backend._make_key(metadata_key) if backend.backend_name == "redis" else metadata_key
    backend._client.set(raw_key, b"corrupt")
    # Bypass Django signals solely to create a distinguishable fresh DB result.
    CachedItem.objects.filter(pk=row.pk).update(value=2)
    assert cache.get(CachedItem.objects.filter(pk=row.pk)) is None
    with django_assert_num_queries(1):
        assert cache.get_or_set(CachedItem.objects.filter(pk=row.pk))[0].value == 2
    assert backend.get(old)[0].value == 1
    cache.invalidate_model(CachedItem._meta.label_lower)
    assert cache.get_or_set(CachedItem.objects.filter(pk=row.pk))[0].value == 2


def test_real_concurrent_generation_initialization(remote_cache, monkeypatch):
    first = remote_cache.cache._state.backend
    second = remote_cache.new_cache()._state.backend
    barrier = Barrier(2)

    def held_get(client):
        original = client.get
        first_call = True

        def get(key):
            nonlocal first_call
            raw = original(key)
            if first_call:
                first_call = False
                assert raw is None
                barrier.wait(timeout=5)
            return raw

        return get

    for backend in (first, second):
        client = backend.backend._client
        monkeypatch.setattr(client, "get", held_get(client))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(backend.get_generation, "cold-scope") for backend in (first, second)]
        tokens = [future.result(timeout=10) for future in futures]
    assert tokens[0] == tokens[1]


@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_uncommitted_rows_are_not_published(remote_cache, rollback):
    row = CachedItem.objects.create(name="Nova", value=1)
    cache = remote_cache.cache
    peer = remote_cache.new_cache()
    with transaction.atomic():
        CachedItem.objects.filter(pk=row.pk).update(value=2)
        assert cache.get_or_set(CachedItem.objects.filter(pk=row.pk))[0].value == 2
        assert peer.get(CachedItem.objects.filter(pk=row.pk)) is None
        if rollback:
            transaction.set_rollback(True)
    assert peer.get(CachedItem.objects.filter(pk=row.pk)) is None
    assert peer.get_or_set(CachedItem.objects.filter(pk=row.pk))[0].value == (1 if rollback else 2)


def test_failed_post_commit_rotation_preserves_write_and_retries(signal_cache, monkeypatch, caplog):
    row = CachedItem.objects.create(name="Nova", value=1)
    cache = signal_cache
    backend = cache._state.backend.backend
    cache.get_or_set(CachedItem.objects.filter(pk=row.pk))
    old = cache._generate_key(CachedItem.objects.filter(pk=row.pk))[0]

    def unavailable(*args, **kwargs):
        raise GenerationUnavailableError("simulated metadata transport failure")

    with monkeypatch.context() as failure:
        failure.setattr(backend, "rotate_generation", unavailable)
        failure.setattr(backend, "delete", unavailable)
        with transaction.atomic():
            row.value = 2
            row.save(update_fields=["value"])
        assert CachedItem.objects.get(pk=row.pk).value == 2
        assert backend.get(old)[0].value == 1
        assert cache.get(CachedItem.objects.filter(pk=row.pk)) is None
        assert cache.get_or_set(CachedItem.objects.filter(pk=row.pk))[0].value == 2
        assert backend.get(old)[0].value == 1
    assert "Shared cache generation rotation failed" in caplog.text
    assert cache.get(CachedItem.objects.filter(pk=row.pk)) is None
    assert cache.get_or_set(CachedItem.objects.filter(pk=row.pk))[0].value == 2

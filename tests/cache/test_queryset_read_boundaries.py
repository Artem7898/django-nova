"""Transaction bypass must precede backend I/O and use one routed database."""

from unittest.mock import Mock

import pytest
from django.db import router

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_read_consistency import (
    make_query,
    rolled_back_transaction,
    values,
)
from tests.models import CachedItem

pytestmark = pytest.mark.django_db(transaction=True)


class GenerationMemoryBackend(MemoryCacheBackend):
    def get_generation(self, scope):
        return "0" * 32

    def rotate_generation(self, scope):
        return "1" * 32


@pytest.mark.parametrize("mode", ["atomic", "manual"])
@pytest.mark.parametrize("reader", ["get", "get_or_set"])
def test_transaction_bypass_performs_no_backend_io(mode, reader, monkeypatch):
    row = CachedItem.objects.create(name="no-cache-io", value=1)
    backend = GenerationMemoryBackend()
    cache = QuerySetCache(backend=backend)
    assert values(cache.get_or_set(make_query(row.pk))) == [1]
    calls = []
    for name in ("get_generation", "rotate_generation", "get", "set", "delete", "clear"):
        call = Mock(side_effect=AssertionError(f"Unexpected transactional cache call: {name}"))
        monkeypatch.setattr(backend, name, call)
        calls.append(call)

    with rolled_back_transaction(mode):
        CachedItem.objects.filter(pk=row.pk).update(value=2)
        result = getattr(cache, reader)(make_query(row.pk))
        if reader == "get":
            assert result is None
        else:
            assert values(result) == [2]
    for call in calls:
        call.assert_not_called()


@pytest.mark.parametrize("reader", ["get", "get_or_set"])
def test_database_is_resolved_once_per_cache_read(reader, monkeypatch, django_assert_num_queries):
    row = CachedItem.objects.create(name="routing", value=1)
    cache = QuerySetCache(backend=MemoryCacheBackend())
    if reader == "get":
        cache.get_or_set(make_query(row.pk).using("default"))
    query = make_query(row.pk)
    choose_database = Mock(side_effect=["default", "must-not-be-resolved-again"])
    monkeypatch.setattr(router, "db_for_read", choose_database)

    with django_assert_num_queries(0 if reader == "get" else 1):
        assert values(getattr(cache, reader)(query)) == [1]
    choose_database.assert_called_once()
    # Pin the clone only; keep the caller's routing decision deferred.
    assert query._db is None

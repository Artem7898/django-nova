"""Cache hits must respect transactions; cache fills must query fresh rows."""

from contextlib import contextmanager

import pytest
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.generation import GenerationUnavailableError
from nova.cache.queryset_cache import QuerySetCache
from tests.models import CachedItem

pytestmark = pytest.mark.django_db(transaction=True)


def make_query(pk, shape="models"):
    query = CachedItem.objects.filter(pk=pk).order_by("pk")
    if shape == "values":
        return query.values("value")
    if shape == "tuples":
        return query.values_list("value")
    if shape == "flat":
        return query.values_list("value", flat=True)
    if shape == "named":
        return query.values_list("value", named=True)
    return query


def values(rows, shape="models"):
    if shape == "values":
        return [row["value"] for row in rows]
    if shape == "tuples":
        return [row[0] for row in rows]
    if shape == "flat":
        return list(rows)
    return [row.value for row in rows]


@contextmanager
def rolled_back_transaction(mode):
    """Keep each case isolated, including manual autocommit management."""
    if mode == "atomic":
        with transaction.atomic():
            try:
                yield
            finally:
                transaction.set_rollback(True)
    else:
        assert transaction.get_autocommit()
        transaction.set_autocommit(False)
        try:
            yield
        finally:
            transaction.rollback()
            transaction.set_autocommit(True)


def check_evaluated_queryset(cache, operation, shape):
    row = CachedItem.objects.create(name="snapshot", value=1)
    pk = row.pk
    query = make_query(pk, shape)
    assert values(cache.get_or_set(query), shape) == [1]
    # Also evaluate the caller's QuerySet explicitly: a correct cache
    # implementation may have evaluated a clone on the first fill.
    assert values(list(query), shape) == [1]
    if operation == "save":
        row.value = 2
        row.save(update_fields=["value"])
        expected = [2]
    else:
        row.delete()
        expected = []
    # Explicit successful invalidation isolates this from signal wiring.
    cache.invalidate_model(CachedItem._meta.label_lower, db=query.db)
    with CaptureQueriesContext(connections[query.db]) as captured:
        result = values(cache.get_or_set(query), shape)
    assert result == expected, (
        f"The evaluated QuerySet republished {result} after {operation}; "
        f"expected {expected}, SQL queries={len(captured)}"
    )
    assert len(captured) == 1, "A cache miss must read a fresh SQL result"
    # Nova must not clear or overwrite the caller's own Django result cache.
    with CaptureQueriesContext(connections[query.db]) as captured:
        assert values(list(query), shape) == [1]
    assert len(captured) == 0
    with CaptureQueriesContext(connections[query.db]) as captured:
        assert values(cache.get_or_set(make_query(pk, shape)), shape) == expected
    assert len(captured) == 0, "The newly filled result must remain cacheable"


def check_transaction_hit(cache, mode, reader):
    row = CachedItem.objects.create(name="own-write", value=1)
    assert values(cache.get_or_set(make_query(row.pk))) == [1]
    with rolled_back_transaction(mode):
        # Deliberately bypass signals: read-your-writes inside the transaction
        # cannot depend on a callback that only runs after a successful commit.
        CachedItem.objects.filter(pk=row.pk).update(value=2)
        result = getattr(cache, reader)(make_query(row.pk))
        if reader == "get":
            assert result is None, "A transactional cache lookup must report a miss"
        else:
            assert values(result) == [2], "A warm cache hid this transaction's own write"
    assert CachedItem.objects.get(pk=row.pk).value == 1
    # The rollback preserved the previously committed result in the cache.
    with CaptureQueriesContext(connections["default"]) as captured:
        assert values(cache.get_or_set(make_query(row.pk))) == [1]
    assert len(captured) == 0


def check_transaction_miss(cache, mode):
    row = CachedItem.objects.create(name="uncommitted-fill", value=1)
    with rolled_back_transaction(mode):
        CachedItem.objects.filter(pk=row.pk).update(value=2)
        assert values(cache.get_or_set(make_query(row.pk))) == [2]
    assert CachedItem.objects.get(pk=row.pk).value == 1
    assert cache.get(make_query(row.pk)) is None, "Rolled-back rows escaped into the cache"
    assert values(cache.get_or_set(make_query(row.pk))) == [1]


def check_autocommit_hit(cache):
    row = CachedItem.objects.create(name="control", value=1)
    assert transaction.get_autocommit()
    with CaptureQueriesContext(connections["default"]) as captured:
        assert values(cache.get_or_set(make_query(row.pk))) == [1]
    assert len(captured) == 1
    with CaptureQueriesContext(connections["default"]) as captured:
        assert values(cache.get_or_set(make_query(row.pk))) == [1]
    assert len(captured) == 0


@pytest.fixture
def local_cache():
    return QuerySetCache(backend=MemoryCacheBackend())


@pytest.mark.parametrize("operation", ["save", "delete"])
@pytest.mark.parametrize("shape", ["models", "values", "tuples", "flat", "named"])
def test_evaluated_queryset_is_not_republished_after_invalidation(local_cache, operation, shape):
    check_evaluated_queryset(local_cache, operation, shape)


@pytest.mark.parametrize("mode", ["atomic", "manual"])
@pytest.mark.parametrize("reader", ["get", "get_or_set"])
def test_warm_cache_does_not_hide_transaction_writes(local_cache, mode, reader):
    check_transaction_hit(local_cache, mode, reader)


@pytest.mark.parametrize("mode", ["atomic", "manual"])
def test_transaction_miss_does_not_publish_uncommitted_rows(local_cache, mode):
    check_transaction_miss(local_cache, mode)


def test_autocommit_still_uses_cached_results(local_cache):
    check_autocommit_hit(local_cache)


def test_evaluated_queryset_fallback_reads_fresh_rows():
    class UnavailableBackend(MemoryCacheBackend):
        def get_generation(self, scope):
            raise GenerationUnavailableError("metadata unavailable")

        def rotate_generation(self, scope):
            raise GenerationUnavailableError("metadata unavailable")

    row = CachedItem.objects.create(name="fallback", value=1)
    query = make_query(row.pk)
    assert values(list(query)) == [1]
    CachedItem.objects.filter(pk=row.pk).update(value=2)
    cache = QuerySetCache(backend=UnavailableBackend())
    assert values(cache.get_or_set(query)) == [2], "Fallback reused the caller's stale result cache"


def test_nested_savepoint_read_observes_rollback(local_cache):
    row = CachedItem.objects.create(name="savepoint", value=1)
    with transaction.atomic():
        CachedItem.objects.filter(pk=row.pk).update(value=2)
        assert values(local_cache.get_or_set(make_query(row.pk))) == [2]
        with transaction.atomic():
            CachedItem.objects.filter(pk=row.pk).update(value=3)
            assert values(local_cache.get_or_set(make_query(row.pk))) == [3]
            transaction.set_rollback(True)
        assert values(local_cache.get_or_set(make_query(row.pk))) == [2]
        transaction.set_rollback(True)
    assert values(local_cache.get_or_set(make_query(row.pk))) == [1]


def test_cache_miss_does_not_publish_mutations_of_evaluated_model_objects(local_cache):
    row = CachedItem.objects.create(name="unsaved-mutation", value=1)
    query = make_query(row.pk)
    original = list(query)
    instance = original[0]
    instance.value = 999  # Not saved; the database still contains value=1.
    assert values(local_cache.get_or_set(query)) == [1], (
        "Unsaved Python state was cached as DB data"
    )
    assert instance.value == 999, "Refreshing a cache fill must not mutate the caller's object"

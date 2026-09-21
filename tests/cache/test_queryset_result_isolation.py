"""A caller's unsaved mutations must never become another caller's cache hit."""

from unittest.mock import Mock

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_cache_failure_recovery import QueryStub
from tests.cache.test_related_invalidation import project, query_for
from tests.cache.test_related_invalidation import relations as relations
from tests.models import CachedItem


@pytest.fixture
def memory_cache():
    return QuerySetCache(backend=MemoryCacheBackend())


@pytest.fixture
def rows_query(transactional_db):
    first = CachedItem.objects.create(name="first", value=1)
    second = CachedItem.objects.create(name="second", value=2)
    return CachedItem.objects.filter(pk__in=[first.pk, second.pk]).order_by("pk")


def read_for_mutation(cache, query, entry):
    if entry == "fill":
        return cache.get_or_set(query)
    cache.get_or_set(query)
    with CaptureQueriesContext(connection) as captured:
        result = getattr(cache, entry)(query)
    assert len(captured) == 0
    assert result is not None
    return result


def check_rows_isolated(cache, query, entry, mutation):
    rows = read_for_mutation(cache, query, entry)
    expected = [(row.pk, row.name, row.value) for row in query.all()]
    if mutation == "clear":
        rows.clear()
    elif mutation == "append":
        rows.append(CachedItem(name="unsaved", value=999))
    elif mutation == "reverse":
        rows.reverse()
    elif mutation == "field":
        rows[0].value = 999
        rows[0].name = "unsaved"
        rows[0].private_note = {"secret": ["caller-only"]}
    else:
        rows[0]._state.adding = True
        rows[0]._state.db = "caller-only"

    peer = QuerySetCache(_state=cache._state)
    with CaptureQueriesContext(connection) as captured:
        for read in (peer.get, cache.get_or_set):
            fresh = read(query)
            assert fresh is not None
            assert [(row.pk, row.name, row.value) for row in fresh] == expected
            assert all(not row._state.adding and row._state.db == "default" for row in fresh)
            assert all(not hasattr(row, "private_note") for row in fresh)
            assert fresh is not rows
    assert len(captured) == 0, "Isolation must preserve a usable hit, not force a database read"
    assert list(query.values_list("pk", "name", "value")) == expected


@pytest.mark.parametrize("entry", ["fill", "get", "get_or_set"])
@pytest.mark.parametrize("mutation", ["clear", "append", "reverse", "field", "state"])
def test_returned_rows_are_isolated(memory_cache, rows_query, entry, mutation):
    check_rows_isolated(memory_cache, rows_query, entry, mutation)


def check_related_isolated(cache, case, entry, kind):
    query = query_for(case, kind)
    rows = read_for_mutation(cache, query, entry)
    expected = project(list(query.all()), kind)
    if kind == "author":
        rows[0].author.name = "unsaved"
    elif kind == "profile":
        rows[0].author.profile.label = "unsaved"
    elif kind == "tag":
        rows[0].tags.all()[0].name = "unsaved"
    else:
        rows[0].comments.all()[0].text = "unsaved"

    with CaptureQueriesContext(connection) as captured:
        fresh = cache.get(query)
        assert fresh is not None
        assert project(fresh, kind) == expected
        assert fresh[0] is not rows[0]
        assert project(cache.get_or_set(query), kind) == expected
    assert len(captured) == 0, "Loaded relation graphs must survive copying without extra SQL"
    assert project(list(query.all()), kind) == expected


@pytest.mark.parametrize("entry", ["fill", "get", "get_or_set"])
@pytest.mark.parametrize("kind", ["author", "profile", "tag", "comment"])
def test_related_objects_are_isolated(memory_cache, relations, entry, kind):
    check_related_isolated(memory_cache, relations, entry, kind)


@pytest.mark.parametrize("entry", ["fill", "get", "get_or_set"])
def test_prefetched_list_mutation_is_isolated(memory_cache, relations, entry):
    query = query_for(relations, "tag")
    rows = read_for_mutation(memory_cache, query, entry)
    rows[0].tags.all()._result_cache.clear()
    with CaptureQueriesContext(connection) as captured:
        fresh = memory_cache.get(query)
        assert fresh is not None
        assert project(fresh, "tag") == [["tag-old"]]
    assert len(captured) == 0


def check_json_isolated(cache, base_query, entry, shape):
    query = base_query.annotate(
        payload=models.Value({"nested": {"items": ["safe"]}}, output_field=models.JSONField())
    )
    if shape == "dict":
        query = query.values("payload")
    elif shape == "tuple":
        query = query.values_list("payload")
    elif shape == "named":
        query = query.values_list("payload", named=True)
    elif shape == "flat":
        query = query.values_list("payload", flat=True)

    def payload(row):
        if shape == "dict":
            return row["payload"]
        if shape in {"tuple", "named"}:
            return row[0]
        return row if shape == "flat" else row.payload

    rows = read_for_mutation(cache, query, entry)
    payload(rows[0])["nested"]["items"].append("unsaved")
    with CaptureQueriesContext(connection) as captured:
        fresh = cache.get(query)
        assert fresh is not None
        assert payload(fresh[0]) == {"nested": {"items": ["safe"]}}
        assert type(fresh[0]) is type(rows[0])
        if shape == "named":
            assert fresh[0].payload == payload(fresh[0])
    assert len(captured) == 0


@pytest.mark.parametrize("entry", ["fill", "get", "get_or_set"])
@pytest.mark.parametrize("shape", ["model", "dict", "tuple", "named", "flat"])
def test_nested_json_and_result_shape_are_preserved(memory_cache, rows_query, entry, shape):
    check_json_isolated(memory_cache, rows_query, entry, shape)


def test_lazy_relation_access_does_not_populate_other_readers(memory_cache, relations):
    query = relations.Article.objects.filter(pk=relations.article.pk)
    first = memory_cache.get_or_set(query)
    assert "author" not in first[0]._state.fields_cache
    assert first[0].author.name == "author-old"
    first[0].author.name = "unsaved"
    with CaptureQueriesContext(connection) as captured:
        second = memory_cache.get(query)
        assert second is not None
        assert "author" not in second[0]._state.fields_cache
    assert len(captured) == 0
    assert second[0].author.name == "author-old"


def test_loading_deferred_field_does_not_populate_other_readers(memory_cache, rows_query):
    query = rows_query.only("id", "name")
    first = memory_cache.get_or_set(query)
    assert "value" in first[0].get_deferred_fields()
    assert first[0].value == 1
    first[0].value = 999
    with CaptureQueriesContext(connection) as captured:
        second = memory_cache.get(query)
        assert second is not None
        assert "value" in second[0].get_deferred_fields()
    assert len(captured) == 0
    assert second[0].value == 1


def test_loaded_reverse_relation_preserves_identity_within_each_graph(memory_cache, relations):
    query = query_for(relations, "comment")
    first = memory_cache.get_or_set(query)
    with CaptureQueriesContext(connection) as captured:
        second = memory_cache.get(query)
        assert second is not None
        assert second[0] is not first[0]
        assert second[0].comments.all()[0].article is second[0]
    assert len(captured) == 0


class UncopyableValue:
    def __reduce_ex__(self, protocol):
        raise TypeError("Cannot snapshot this result")


def test_snapshot_failure_returns_database_result_without_publication(memory_cache, caplog):
    query = QueryStub()
    value = UncopyableValue()
    query.rows = [value]
    assert memory_cache.get_or_set(query) == [value]
    assert memory_cache.get(query) is None
    assert memory_cache.stats["currsize"] == 0
    assert "snapshot" in caplog.text.lower()


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_bad_snapshot_is_a_miss_not_a_shared_reference(memory_cache, entry, caplog):
    query = QueryStub(value=2)
    key = memory_cache._generate_key(query)[0]
    memory_cache._state.backend.set(key, [UncopyableValue()])
    result = getattr(memory_cache, entry)(query)
    assert result == (None if entry == "get" else [2])
    assert query.executions == (0 if entry == "get" else 1)
    assert "snapshot" in caplog.text.lower()


def test_snapshot_failure_does_not_mask_database_errors(memory_cache):
    query = QueryStub()
    key = memory_cache._generate_key(query)[0]
    memory_cache._state.backend.set(key, [UncopyableValue()])
    query.error = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        memory_cache.get_or_set(query)


def test_process_control_exception_is_not_swallowed(memory_cache, monkeypatch):
    class Cancelled(BaseException):
        pass

    class InterruptedValue:
        def __reduce_ex__(self, protocol):
            raise Cancelled

    query = QueryStub()
    query.rows = [InterruptedValue()]
    write = Mock()
    monkeypatch.setattr(memory_cache._state.backend, "set", write)
    with pytest.raises(Cancelled):
        memory_cache.get_or_set(query)
    write.assert_not_called()


def test_empty_result_remains_a_cache_hit(memory_cache):
    query = QueryStub()
    query.rows = []
    result = memory_cache.get_or_set(query)
    result.append("caller-only")
    assert memory_cache.get(query) == []
    assert memory_cache.get_or_set(query) == []
    assert query.executions == 1


def test_isolated_model_and_materialized_queryset_keep_their_classes(memory_cache):
    with isolate_apps():

        class TemporaryRecord(models.Model):
            name = models.CharField(max_length=20)

            class Meta:
                app_label = "nova_snapshot_isolated"

        original = TemporaryRecord(id=1, name="original")
        loaded = models.QuerySet(model=TemporaryRecord)
        loaded._result_cache = [original]
        original.loaded = loaded  # A cycle through a materialized collection.
        query = QueryStub()
        query.rows = [original]
        result = memory_cache.get_or_set(query)
        result[0].name = "unsaved"
        fresh = memory_cache.get(query)
        assert fresh is not None
        assert type(fresh[0]) is TemporaryRecord
        assert fresh[0].name == "original"
        assert fresh[0].loaded.model is TemporaryRecord
        assert fresh[0].loaded[0] is fresh[0]
        assert original.name == "unsaved"


def test_failed_snapshot_can_be_replaced_by_a_later_cacheable_result(memory_cache):
    query = QueryStub()
    value = UncopyableValue()
    query.rows = [value]
    assert memory_cache.get_or_set(query) == [value]
    query.rows = [{"items": [1]}]
    returned = memory_cache.get_or_set(query)
    returned[0]["items"].append(2)
    assert memory_cache.get(query) == [{"items": [1]}]
    assert query.executions == 2

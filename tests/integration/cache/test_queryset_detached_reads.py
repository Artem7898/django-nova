"""One deserialization per hit, with independent ORM graphs on real servers."""

from unittest.mock import Mock

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from nova.cache import queryset_cache as cache_module
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache.test_queryset_remote_backends import remote_cache as remote_cache

pytestmark = pytest.mark.integration

SCENARIOS = [
    "plain",
    "select_related",
    "prefetch_related",
    "json-model",
    "json-dict",
    "json-tuple",
    "json-named",
    "json-flat",
]


def make_query(case, scenario):
    query = case.Article.objects.filter(pk=case.article.pk)
    if scenario == "select_related":
        return query.select_related("author__profile")
    if scenario == "prefetch_related":
        return query.prefetch_related("tags")
    if scenario.startswith("json-"):
        query = query.annotate(
            payload=models.Value({"items": [{"name": "safe"}]}, output_field=models.JSONField())
        )
        if scenario == "json-dict":
            return query.values("payload")
        if scenario == "json-tuple":
            return query.values_list("payload")
        if scenario == "json-named":
            return query.values_list("payload", named=True)
        if scenario == "json-flat":
            return query.values_list("payload", flat=True)
    return query


def payload(row, scenario):
    if scenario == "json-dict":
        return row["payload"]
    if scenario in {"json-tuple", "json-named"}:
        return row[0]
    if scenario == "json-flat":
        return row
    return row.payload


def fingerprint(rows, scenario):
    if scenario.startswith("json-"):
        return [payload(row, scenario)["items"][0]["name"] for row in rows]
    result = [(row.pk, row.title) for row in rows]
    if scenario == "select_related":
        return result, [(row.author.name, row.author.profile.label) for row in rows]
    if scenario == "prefetch_related":
        return result, [[tag.name for tag in row.tags.all()] for row in rows]
    return result


def mutate(rows, scenario):
    if scenario.startswith("json-"):
        payload(rows[0], scenario)["items"][0]["name"] = "unsaved"
    else:
        rows[0].title = "unsaved"
        if scenario == "select_related":
            rows[0].author.name = "unsaved"
            rows[0].author.profile.label = "unsaved"
        if scenario == "prefetch_related":
            rows[0].tags.all()[0].name = "unsaved"
            rows[0].tags.all()._result_cache.clear()
    rows.clear()


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_owned_hit_uses_one_decode_and_keeps_nested_data_isolated(
    remote_cache, relations, monkeypatch, scenario, entry
):
    cache = remote_cache.cache
    query = make_query(relations, scenario)
    expected = fingerprint(cache.get_or_set(query), scenario)
    serializer = cache._state.backend.backend._serializer
    snapshot = Mock(side_effect=AssertionError("A detached hit must not be copied again"))
    dumps = Mock(side_effect=AssertionError("A cache hit must not serialize the result again"))
    loads = Mock(wraps=serializer.loads)
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    monkeypatch.setattr(serializer, "dumps", dumps)
    monkeypatch.setattr(serializer, "loads", loads)
    with CaptureQueriesContext(connection) as sql:
        first = getattr(cache, entry)(query)
        assert first is not None
        assert fingerprint(first, scenario) == expected
        row_type = type(first[0])
        mutate(first, scenario)
        second = getattr(cache, entry)(query)
        assert second is not None
        assert type(second[0]) is row_type
        assert fingerprint(second, scenario) == expected
        if scenario == "json-named":
            assert second[0].payload["items"][0]["name"] == "safe"
    assert len(sql) == 0
    assert loads.call_count == 2
    snapshot.assert_not_called()
    dumps.assert_not_called()


def test_native_fill_serializes_once_and_preserves_read_isolation(
    remote_cache, relations, monkeypatch
):
    cache = remote_cache.cache
    query = make_query(relations, "prefetch_related")
    serializer = cache._state.backend.backend._serializer
    snapshot = Mock(wraps=cache_module.snapshot_rows)
    dumps = Mock(wraps=serializer.dumps)
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    monkeypatch.setattr(serializer, "dumps", dumps)
    first = cache.get_or_set(query)
    snapshot.assert_not_called()
    assert dumps.call_count == 1
    first[0].tags.all()[0].name = "unsaved"
    with CaptureQueriesContext(connection) as sql:
        assert cache.get(query)[0].tags.all()[0].name == "tag-old"
    assert len(sql) == 0
    snapshot.assert_not_called()
    assert dumps.call_count == 1


def test_deferred_fields_and_model_state_remain_per_read(remote_cache, relations, monkeypatch):
    cache = remote_cache.cache
    query = relations.Article.objects.filter(pk=relations.article.pk).only("id")
    cache.get_or_set(query)
    snapshot = Mock(side_effect=AssertionError("Unexpected copy"))
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    first = cache.get(query)
    assert "title" in first[0].get_deferred_fields()
    assert first[0].title == "article"  # This caller alone loads the deferred field.
    first[0].title = "unsaved"
    first[0]._state.adding = True
    first[0]._state.db = "caller-only"
    with CaptureQueriesContext(connection) as sql:
        second = cache.get(query)
        assert "title" in second[0].get_deferred_fields()
        assert second[0]._state.adding is False
        assert second[0]._state.db == "default"
    assert len(sql) == 0
    snapshot.assert_not_called()


def test_loaded_reverse_cycle_stays_inside_each_result(remote_cache, relations, monkeypatch):
    cache = remote_cache.cache
    query = relations.Article.objects.filter(pk=relations.article.pk).prefetch_related("comments")
    cache.get_or_set(query)
    snapshot = Mock(side_effect=AssertionError("Unexpected copy"))
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    with CaptureQueriesContext(connection) as sql:
        first = cache.get(query)
        second = cache.get(query)
        assert first[0].comments.all()[0].article is first[0]
        assert second[0].comments.all()[0].article is second[0]
        assert first[0] is not second[0]
        first[0].comments.all()[0].text = "unsaved"
        assert second[0].comments.all()[0].text == "comment-old"
    assert len(sql) == 0
    snapshot.assert_not_called()

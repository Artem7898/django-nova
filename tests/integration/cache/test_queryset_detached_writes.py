"""Independent storage after real ORM fills on Redis and Memcached."""

from unittest.mock import Mock

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from nova.cache import invalidation
from nova.cache import queryset_cache as cache_module
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache.test_queryset_detached_reads import (
    SCENARIOS,
    fingerprint,
    make_query,
    mutate,
)
from tests.integration.cache.test_queryset_remote_backends import remote_cache as remote_cache

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("independent_reader", [False, True])
def test_native_fill_captures_orm_graph_without_intermediate_snapshot(
    remote_cache, relations, monkeypatch, scenario, independent_reader
):
    cache = remote_cache.cache
    query = make_query(relations, scenario)
    expected = fingerprint(list(query.all()), scenario)
    raw = cache._state.backend.backend
    serializer = raw._serializer
    snapshot = Mock(
        side_effect=AssertionError("A native fill must not create an intermediate copy")
    )
    dumps, loads = Mock(wraps=serializer.dumps), Mock(wraps=serializer.loads)
    publish = Mock(wraps=raw.set)
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    monkeypatch.setattr(serializer, "dumps", dumps)
    monkeypatch.setattr(serializer, "loads", loads)
    monkeypatch.setattr(raw, "set", publish)
    with CaptureQueriesContext(connection) as sql:
        rows = cache.get_or_set(query)
        assert fingerprint(rows, scenario) == expected
    assert len(sql) == (2 if scenario == "prefetch_related" else 1)
    assert publish.call_args.args[1] is rows
    dumps.assert_called_once()
    loads.assert_not_called()
    snapshot.assert_not_called()
    row_type = type(rows[0])
    mutate(rows, scenario)
    reader = remote_cache.new_cache() if independent_reader else cache
    with CaptureQueriesContext(connection) as sql:
        hit = reader.get(query)
        assert hit is not None and type(hit[0]) is row_type
        assert fingerprint(hit, scenario) == expected
        mutate(hit, scenario)
        assert fingerprint(reader.get_or_set(query), scenario) == expected
    assert len(sql) == 0
    dumps.assert_called_once()
    snapshot.assert_not_called()


def test_native_empty_fill_is_persisted_and_isolated(remote_cache, relations, monkeypatch):
    cache = remote_cache.cache
    query = relations.Article.objects.filter(pk=-1)
    snapshot = Mock(side_effect=AssertionError("Unexpected snapshot"))
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    rows = cache.get_or_set(query)
    assert rows == []
    rows.append(relations.article)
    with CaptureQueriesContext(connection) as sql:
        assert remote_cache.new_cache().get(query) == []
    assert len(sql) == 0
    snapshot.assert_not_called()


def test_native_fill_preserves_deferred_fields_and_model_state(
    remote_cache, relations, monkeypatch
):
    cache = remote_cache.cache
    query = relations.Article.objects.filter(pk=relations.article.pk).only("id")
    snapshot = Mock(side_effect=AssertionError("Unexpected snapshot"))
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    with CaptureQueriesContext(connection) as sql:
        rows = cache.get_or_set(query)
    assert len(sql) == 1
    assert "title" in rows[0].get_deferred_fields()
    assert rows[0].title == "article"
    rows[0].title = "caller-only"
    rows[0]._state.adding = True
    rows[0]._state.db = "caller-only"
    with CaptureQueriesContext(connection) as sql:
        stored = remote_cache.new_cache().get(query)[0]
        assert "title" in stored.get_deferred_fields()
        assert stored._state.adding is False
        assert stored._state.db == "default"
    assert len(sql) == 0
    snapshot.assert_not_called()


def test_native_fill_keeps_cycles_inside_each_graph(remote_cache, relations, monkeypatch):
    cache = remote_cache.cache
    query = relations.Article.objects.filter(pk=relations.article.pk).prefetch_related("comments")
    snapshot = Mock(side_effect=AssertionError("Unexpected snapshot"))
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    with CaptureQueriesContext(connection) as sql:
        rows = cache.get_or_set(query)
        assert rows[0].comments.all()[0].article is rows[0]
        rows[0].comments.all()[0].text = "caller-only"
    assert len(sql) == 2
    with CaptureQueriesContext(connection) as sql:
        stored = remote_cache.new_cache().get(query)
        assert stored[0].comments.all()[0].article is stored[0]
        assert stored[0].comments.all()[0].text == "comment-old"
    assert len(sql) == 0
    snapshot.assert_not_called()


def test_native_serialization_error_returns_orm_result_and_recovers(
    remote_cache, relations, monkeypatch, caplog
):
    cache = remote_cache.cache
    query = make_query(relations, "prefetch_related")
    raw = cache._state.backend.backend
    original = raw._serializer.dumps
    snapshot = Mock(side_effect=AssertionError("Unexpected snapshot"))
    monkeypatch.setattr(cache_module, "snapshot_rows", snapshot)
    monkeypatch.setattr(
        raw._serializer, "dumps", Mock(side_effect=TypeError("serialization failed"))
    )
    with CaptureQueriesContext(connection) as sql:
        rows = cache.get_or_set(query)
        assert rows[0].title == "article"
        assert rows[0].tags.all()[0].name == "tag-old"
    assert len(sql) == 2
    assert not cache._state.model_keys
    assert cache.get(query) is None
    assert "Shared cache write failed" in caplog.text
    monkeypatch.setattr(raw._serializer, "dumps", original)
    cache.get_or_set(query)
    with CaptureQueriesContext(connection) as sql:
        assert remote_cache.new_cache().get(query)[0].title == "article"
    assert len(sql) == 0
    snapshot.assert_not_called()


@pytest.mark.parametrize("applied", [False, True])
def test_native_transport_error_preserves_result_and_stored_isolation(
    remote_cache, relations, monkeypatch, applied
):
    cache = remote_cache.cache
    query = make_query(relations, "prefetch_related")
    raw = cache._state.backend.backend
    key = cache._generate_key(query)[0]
    wire_key = raw._make_key(key) if hasattr(raw, "_make_key") else key
    original = raw._client.set

    def fail_write(candidate, payload, *args, **kwargs):
        if candidate != wire_key:
            return original(candidate, payload, *args, **kwargs)
        if applied:
            original(candidate, payload, *args, **kwargs)
        raise OSError("data write acknowledgement lost")

    monkeypatch.setattr(raw._client, "set", fail_write)
    rows = cache.get_or_set(query)
    assert rows[0].title == "article"
    assert not cache._state.model_keys
    rows[0].tags.all()[0].name = "caller-only"
    rows.clear()
    monkeypatch.setattr(raw._client, "set", original)
    peer = remote_cache.new_cache()
    with CaptureQueriesContext(connection) as sql:
        cached = peer.get(query)
        if applied:
            assert cached[0].tags.all()[0].name == "tag-old"
        else:
            assert cached is None
    assert len(sql) == 0
    assert peer.get_or_set(query)[0].tags.all()[0].name == "tag-old"


@pytest.mark.parametrize("stage", ["serializer", "transport"])
def test_commit_after_generation_check_keeps_late_fill_in_old_generation(
    remote_cache, relations, monkeypatch, stage
):
    reader, writer = remote_cache.cache, remote_cache.new_cache()
    invalidation.connect_invalidation(relations.Article, cache=writer)
    raw = reader._state.backend.backend
    query = make_query(relations, "prefetch_related")
    old_key = reader._generate_key(query)[0]
    committed = False

    def commit_once():
        nonlocal committed
        if not committed:
            committed = True
            with transaction.atomic():
                relations.article.title = "committed"
                relations.article.save(update_fields=["title"])

    if stage == "serializer":
        original = raw._serializer.dumps

        def serialize(value):
            commit_once()
            return original(value)

        monkeypatch.setattr(raw._serializer, "dumps", serialize)
    else:
        original = raw._client.set
        wire_key = raw._make_key(old_key) if hasattr(raw, "_make_key") else old_key

        def publish(key, payload, *args, **kwargs):
            if key == wire_key:
                commit_once()
            return original(key, payload, *args, **kwargs)

        monkeypatch.setattr(raw._client, "set", publish)
    assert reader.get_or_set(query)[0].title == "article"
    assert committed
    assert not writer._state.model_keys
    assert raw.get(old_key)[0].title == "article", "Keep the late stale entry as a control"
    assert reader._generate_key(query)[0] != old_key
    assert reader.get(query) is None
    assert reader.get_or_set(query)[0].title == "committed"

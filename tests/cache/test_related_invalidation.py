"""Real related writes, on_commit invalidation and rollback-preserved cache hits."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Prefetch
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from nova.cache import invalidation
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_shared_cache_generation import SharedBackend


@pytest.fixture
def relations(transactional_db, monkeypatch):
    receivers = []
    for signal in (post_save, post_delete, m2m_changed):
        original = signal.connect

        def track(receiver, *args, _signal=signal, _connect=original, **kwargs):
            receivers.append((_signal, receiver, kwargs.get("sender")))
            return _connect(receiver, *args, **kwargs)

        monkeypatch.setattr(signal, "connect", track)
    before = set(invalidation._CONNECTED_SIGNALS)
    before_m2m = set(getattr(invalidation, "_CONNECTED_M2M_SIGNALS", set()))
    with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "tests.cache.relations_app"]):
        models = [
            apps.get_model("nova_cache_relations_tests", name)
            for name in ("Profile", "Author", "Tag", "Article", "Comment")
        ]
        with connection.schema_editor() as editor:
            for model in models:
                editor.create_model(model)
        profile_model, author_model, tag_model, article_model, comment_model = models
        try:
            profile = profile_model.objects.create(label="profile-old")
            author = author_model.objects.create(name="author-old", profile=profile)
            article = article_model.objects.create(title="article", author=author)
            tag = tag_model.objects.create(name="tag-old")
            other_tag = tag_model.objects.create(name="tag-other")
            article.tags.add(tag)
            comment = comment_model.objects.create(article=article, text="comment-old")
            yield SimpleNamespace(
                Profile=profile_model,
                Author=author_model,
                Tag=tag_model,
                Article=article_model,
                Comment=comment_model,
                profile=profile,
                author=author,
                article=article,
                tag=tag,
                other_tag=other_tag,
                comment=comment,
            )
        finally:
            for signal, receiver, sender in receivers:
                signal.disconnect(receiver, sender=sender)
            invalidation._CONNECTED_SIGNALS.intersection_update(before)
            registry = getattr(invalidation, "_CONNECTED_M2M_SIGNALS", None)
            if registry is not None:
                registry.intersection_update(before_m2m)
            with connection.schema_editor() as editor:
                for model in reversed(models):
                    editor.delete_model(model)


@pytest.fixture(params=["memory", "shared"])
def cache_pair(request, relations):
    backend = MemoryCacheBackend() if request.param == "memory" else SharedBackend()
    reader = QuerySetCache(backend=backend)
    writer = (
        QuerySetCache(_state=reader._state)
        if request.param == "memory"
        else QuerySetCache(backend=backend)
    )
    invalidation.connect_invalidation(relations.Article, cache=writer)
    return reader, writer


@contextmanager
def change_transaction(rollback):
    with transaction.atomic():
        yield
        if rollback:
            transaction.set_rollback(True)


def query_for(case, kind):
    query = case.Article.objects.filter(pk=case.article.pk)
    if kind == "author":
        return query.select_related("author")
    if kind == "profile":
        return query.select_related("author__profile")
    return query.prefetch_related("tags" if kind == "tag" else "comments")


def project(rows, kind):
    if kind == "author":
        return [row.author.name for row in rows]
    if kind == "profile":
        return [row.author.profile.label if row.author.profile else None for row in rows]
    relation, attr = ("tags", "name") if kind == "tag" else ("comments", "text")
    return [sorted(getattr(child, attr) for child in getattr(row, relation).all()) for row in rows]


def check_related_write(case, reader, writer, kind, operation, rollback):
    query = query_for(case, kind)
    before = project(reader.get_or_set(query), kind)
    with CaptureQueriesContext(connection) as captured:
        assert project(reader.get_or_set(query), kind) == before
    assert len(captured) == 0
    old_key = reader._generate_key(query)[0]
    target = getattr(case, kind)
    with change_transaction(rollback):
        if operation == "save":
            attr = {"author": "name", "profile": "label", "tag": "name", "comment": "text"}[kind]
            setattr(target, attr, "changed")
            target.save(update_fields=[attr])
        else:
            target.delete()
        assert reader._state.backend.get(old_key) is not None
        assert reader._generate_key(query)[0] == old_key
        assert reader.get(query) is None
    expected = project(list(query.all()), kind)
    if rollback:
        with CaptureQueriesContext(connection) as captured:
            assert project(reader.get_or_set(query), kind) == before
        assert len(captured) == 0
    else:
        assert reader.get(query) is None, f"Committed {kind} {operation} left a stale hit"
        assert project(reader.get_or_set(query), kind) == expected
        with CaptureQueriesContext(connection) as captured:
            assert project(reader.get_or_set(query), kind) == expected
        assert len(captured) == 0


def check_m2m(case, reader, writer, operation, reverse, rollback):
    query = query_for(case, "tag")
    if operation == "add":
        case.article.tags.clear()
    before = project(reader.get_or_set(query), "tag")
    old_key = reader._generate_key(query)[0]
    manager = case.tag.articles if reverse else case.article.tags
    item = case.article if reverse else case.tag
    with change_transaction(rollback):
        getattr(manager, operation)(*(() if operation == "clear" else (item,)))
        assert reader._state.backend.get(old_key) is not None
        assert reader._generate_key(query)[0] == old_key
    expected = project(list(query.all()), "tag")
    if rollback:
        with CaptureQueriesContext(connection) as captured:
            assert project(reader.get_or_set(query), "tag") == before
        assert len(captured) == 0
    else:
        assert reader.get(query) is None, f"M2M {operation}, reverse={reverse} left a stale hit"
        assert project(reader.get_or_set(query), "tag") == expected


@pytest.mark.parametrize("kind", ["author", "profile", "tag", "comment"])
@pytest.mark.parametrize("operation", ["save", "delete"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_related_write(relations, cache_pair, kind, operation, rollback):
    check_related_write(relations, *cache_pair, kind, operation, rollback)


@pytest.mark.parametrize("operation", ["add", "remove", "clear"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_m2m_change(relations, cache_pair, operation, reverse, rollback):
    check_m2m(relations, *cache_pair, operation, reverse, rollback)


@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_create_in_empty_reverse_relation(relations, cache_pair, rollback):
    reader, _ = cache_pair
    relations.comment.delete()
    query = query_for(relations, "comment")
    assert project(reader.get_or_set(query), "comment") == [[]]
    with change_transaction(rollback):
        relations.Comment.objects.create(article=relations.article, text="new")
    assert project(reader.get_or_set(query), "comment") == ([[]] if rollback else [["new"]])


def test_prefetch_plan_is_part_of_cache_identity(relations, cache_pair):
    reader, _ = cache_pair
    plain = relations.Article.objects.filter(pk=relations.article.pk)
    reader.get_or_set(plain)
    for relation in ("tags", "comments"):
        query = plain.prefetch_related(relation)
        assert reader.get(query) is None, "A different prefetch plan reused a cached result"
        reader.get_or_set(query)
        with CaptureQueriesContext(connection) as captured:
            rows = reader.get_or_set(query)
            assert len(list(getattr(rows[0], relation).all())) == 1
        assert len(captured) == 0


def test_unrelated_write_preserves_root_only_hit(relations, cache_pair):
    reader, _ = cache_pair
    query = relations.Article.objects.filter(pk=relations.article.pk)
    reader.get_or_set(query)
    relations.author.name = "changed"
    relations.author.save(update_fields=["name"])
    with CaptureQueriesContext(connection) as captured:
        assert reader.get(query)[0].title == "article"
    assert len(captured) == 0


def test_custom_prefetch_does_not_collide_with_plain_results(relations, cache_pair):
    reader, _ = cache_pair
    plain = relations.Article.objects.filter(pk=relations.article.pk)
    reader.get_or_set(plain)
    query = plain.prefetch_related(Prefetch("tags", to_attr="picked_tags"))
    assert [tag.name for tag in reader.get_or_set(query)[0].picked_tags] == ["tag-old"]


@pytest.mark.parametrize("rollback_kind", ["inner", "outer", "none"])
def test_m2m_savepoints_obey_outer_commit(relations, cache_pair, rollback_kind):
    reader, _ = cache_pair
    query = query_for(relations, "tag")
    before = project(reader.get_or_set(query), "tag")
    key = reader._generate_key(query)[0]
    with transaction.atomic():
        with transaction.atomic():
            relations.article.tags.clear()
            if rollback_kind == "inner":
                transaction.set_rollback(True)
        assert reader._state.backend.get(key) is not None
        if rollback_kind == "outer":
            transaction.set_rollback(True)
    if rollback_kind == "none":
        assert reader.get(query) is None
        assert project(reader.get_or_set(query), "tag") == [[]]
    else:
        with CaptureQueriesContext(connection) as captured:
            assert project(reader.get_or_set(query), "tag") == before
        assert len(captured) == 0


def test_reconnecting_relation_graph_does_not_duplicate_m2m_callbacks(
    relations, cache_pair, monkeypatch
):
    from unittest.mock import Mock

    _, writer = cache_pair
    spy = Mock(wraps=writer.invalidate_model)
    monkeypatch.setattr(writer, "invalidate_model", spy)
    invalidation.connect_invalidation(relations.Article, cache=writer)
    invalidation.connect_invalidation(relations.Article, cache=writer)
    with transaction.atomic():
        relations.article.tags.add(relations.other_tag)
        spy.assert_not_called()
    spy.assert_called_once_with(relations.Article.tags.through._meta.label_lower, "default")


@pytest.mark.parametrize("path", ["author", "tags"])
def test_join_filter_tracks_related_changes_without_eager_loading(relations, cache_pair, path):
    reader, _ = cache_pair
    target = relations.author if path == "author" else relations.tag
    query = relations.Article.objects.filter(**{f"{path}__name": target.name}).values_list(
        "pk", flat=True
    )
    assert reader.get_or_set(query) == [relations.article.pk]
    target.name = "changed"
    target.save(update_fields=["name"])
    assert reader.get(query) is None
    assert reader.get_or_set(query) == []


def test_prefetch_nested_path_tracks_deep_model(relations, cache_pair):
    reader, _ = cache_pair
    query = relations.Article.objects.filter(pk=relations.article.pk).prefetch_related(
        "author__profile"
    )
    reader.get_or_set(query)
    relations.profile.label = "changed"
    relations.profile.save(update_fields=["label"])
    assert reader.get(query) is None
    assert project(reader.get_or_set(query), "profile") == ["changed"]


@pytest.mark.parametrize("kind", ["author", "tag"])
def test_related_change_fences_fill_after_sql(relations, cache_pair, kind):
    from django.db import models

    reader, _ = cache_pair
    target = getattr(relations, kind)
    changed = False

    class PausedQuerySet(models.QuerySet):
        def __iter__(self):
            nonlocal changed
            iterator = super().__iter__()
            if not changed:
                changed = True
                target.name = "changed"
                target.save(update_fields=["name"])
            return iterator

    query = PausedQuerySet(model=relations.Article, using="default").filter(pk=relations.article.pk)
    query = query.select_related("author") if kind == "author" else query.prefetch_related("tags")
    before = ["author-old"] if kind == "author" else [["tag-old"]]
    assert project(reader.get_or_set(query), kind) == before
    fresh = query_for(relations, kind)
    assert reader.get(fresh) is None
    assert project(reader.get_or_set(fresh), kind) == (
        ["changed"] if kind == "author" else [["changed"]]
    )


@pytest.mark.parametrize("plan", ["prefetch", "subquery", "extra"])
def test_untracked_plans_bypass_cache_and_read_fresh_data(relations, cache_pair, plan):
    from django.db.models import Subquery

    reader, _ = cache_pair
    query = relations.Article.objects.filter(pk=relations.article.pk)
    if plan == "prefetch":
        query = query.prefetch_related(Prefetch("tags", to_attr="picked_tags"))
    elif plan == "subquery":
        query = query.filter(author_id__in=Subquery(relations.Author.objects.values("pk")))
    else:
        query = query.extra(where=["1=1"])
    assert reader.get(query) is None
    assert reader.get_or_set(query)[0].title == "article"
    assert reader.get(query) is None
    # Bulk update intentionally sends no signal: an uncached query must still
    # read it rather than publishing an entry with incomplete dependencies.
    relations.Article.objects.filter(pk=relations.article.pk).update(title="fresh")
    assert reader.get_or_set(query)[0].title == "fresh"


@pytest.mark.parametrize("reason", ["router", "manager"])
def test_implicit_prefetch_plans_are_not_assumed_cacheable(
    relations, cache_pair, reason, monkeypatch
):
    from contextlib import nullcontext

    from django.db import models

    reader, _ = cache_pair
    query = query_for(relations, "tag")
    if reason == "router":

        class DefaultRouter:
            def db_for_read(self, model, **hints):
                return "default"

        context = override_settings(DATABASE_ROUTERS=[DefaultRouter()])
    else:

        class FilteredManager(models.Manager):
            def get_queryset(self):
                return super().get_queryset().filter(name__startswith="tag")

        manager = FilteredManager()
        manager.model = relations.Tag
        monkeypatch.setattr(relations.Tag._meta, "default_manager", manager)
        context = nullcontext()
    with context:
        assert reader.get(query) is None
        assert project(reader.get_or_set(query), "tag") == [["tag-old"]]
        assert reader.get(query) is None

"""Spawn-safe workers for related-query invalidation against real cache servers."""

import os
import traceback
from copy import deepcopy
from unittest.mock import patch

from tests.integration.cache._queryset_process_worker import remote_backend


def related_query(spec):
    from django.apps import apps

    article_model = apps.get_model("nova_cache_relations_tests", "Article")
    query = article_model.objects.filter(pk=spec["article_pk"])
    kind = spec["kind"]
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
    relation, attribute = ("tags", "name") if kind == "tag" else ("comments", "text")
    return [
        sorted(getattr(item, attribute) for item in getattr(row, relation).all()) for row in rows
    ]


def expect_command(channel, expected):
    if not channel.poll(30):
        raise TimeoutError(f"No {expected!r} command from the parent process")
    command = channel.recv()
    assert command == expected, (command, expected)


def mutate(spec):
    """Write via ordinary Django signals without evaluating the cached query."""
    from django.apps import apps

    model_names = {"author": "Author", "profile": "Profile", "tag": "Tag", "comment": "Comment"}
    kind = spec["kind"]
    operation = spec["operation"]
    model = apps.get_model("nova_cache_relations_tests", model_names[kind])
    if operation == "create":
        model.objects.create(article_id=spec["article_pk"], text="changed")
        return
    target = model.objects.get(pk=spec[f"{kind}_pk"])
    if operation in {"add", "remove", "clear"}:
        article_model = apps.get_model("nova_cache_relations_tests", "Article")
        article = article_model.objects.get(pk=spec["article_pk"])
        manager = target.articles if spec["reverse"] else article.tags
        item = article if spec["reverse"] else target
        getattr(manager, operation)(*(() if operation == "clear" else (item,)))
    elif operation == "delete":
        target.delete()
    else:
        attribute = {"author": "name", "profile": "label", "tag": "name", "comment": "text"}[kind]
        setattr(target, attribute, "changed")
        target.save(update_fields=[attribute])


def reader(channel, spec, backend, cache):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    old_key = cache._generate_key(related_query(spec))[0]

    def pause():
        channel.send({"kind": "ready-to-store", "pid": os.getpid(), "key": old_key})
        expect_command(channel, "resume")

    phase = spec.get("pause_fill")
    if phase == "before-set":
        original_set = backend.set

        def delayed_set(key, *args, **kwargs):
            assert key == old_key, "The fill must retain its original generation key"
            # Inside backend.set: the final generation check has already passed.
            pause()
            return original_set(key, *args, **kwargs)

        with patch.object(backend, "set", delayed_set):
            rows = cache.get_or_set(related_query(spec))
    elif phase == "after-sql":
        query = related_query(spec)
        original_iter = type(query).__iter__
        paused = False

        def delayed_iter(self):
            nonlocal paused
            # Django materializes select_related AND prefetch_related here.
            rows = list(original_iter(self))
            if self.model is query.model and not paused:
                paused = True
                pause()
            return iter(rows)

        with patch.object(type(query), "__iter__", delayed_iter):
            rows = cache.get_or_set(query)
    else:
        rows = cache.get_or_set(related_query(spec))

    channel.send(
        {
            "kind": "filled",
            "pid": os.getpid(),
            "key": old_key,
            "values": project(rows, spec["kind"]),
        }
    )
    while True:
        if not channel.poll(30):
            raise TimeoutError("Reader did not receive its next command")
        command = channel.recv()
        if command == "stop":
            return
        assert command == "read", command
        with CaptureQueriesContext(connection) as captured:
            hit = cache.get(related_query(spec)) is not None
            values = project(cache.get_or_set(related_query(spec)), spec["kind"])
        channel.send(
            {
                "kind": "read",
                "values": values,
                "hit": hit,
                "sql_count": len(captured),
                "key": cache._generate_key(related_query(spec))[0],
            }
        )


def writer(channel, spec, cache):
    from django.db import transaction

    assert not cache._state.key_models, "Writer must start with an empty local query index"
    committed = []
    with transaction.atomic():
        mutate(spec)
        transaction.on_commit(lambda: committed.append(True))
        channel.send({"kind": "ready-to-commit", "pid": os.getpid()})
        expect_command(channel, "finish")
        if spec["rollback"]:
            transaction.set_rollback(True)
    assert not cache._state.key_models, "Writer must never register the reader's query"
    channel.send({"kind": "written", "pid": os.getpid(), "committed": bool(committed)})


def worker_main(channel, spec, role):
    connections = None
    try:
        import django
        from django.conf import settings

        os.environ["DJANGO_SETTINGS_MODULE"] = spec["settings_module"]
        # The parent creates temporary relation tables and installs their app.
        # Both runtime changes must be reproduced before initializing Django.
        settings.INSTALLED_APPS = list(spec["installed_apps"])
        settings.DATABASES = deepcopy(spec["databases"])
        django.setup()

        from django.apps import apps
        from django.db import connections

        from nova.cache.invalidation import connect_invalidation
        from nova.cache.queryset_cache import QuerySetCache

        with remote_backend(spec) as backend:
            cache = QuerySetCache(backend=backend, ttl=300)
            connect_invalidation(
                apps.get_model("nova_cache_relations_tests", "Article"), cache=cache
            )
            if role == "reader":
                reader(channel, spec, backend, cache)
            else:
                writer(channel, spec, cache)
    except Exception:
        channel.send({"kind": "error", "traceback": traceback.format_exc()})
        raise
    finally:
        if connections is not None:
            connections.close_all()
        channel.close()

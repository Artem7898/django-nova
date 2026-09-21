"""Related writes must invalidate readers in other OS processes after commit."""

import multiprocessing
import os
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection, connections

from nova.cache.generation import generation_key, generation_scope
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache._queryset_process_worker import remote_backend
from tests.integration.cache._related_process_worker import project, related_query, worker_main

pytestmark = pytest.mark.integration


@contextmanager
def worker(spec, role):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=worker_main, args=(child, spec, role))
    process.start()
    child.close()
    try:
        yield parent, process
        process.join(timeout=10)
        assert not process.is_alive(), f"{role} did not exit after completing its work"
        assert process.exitcode == 0, f"{role} exited with code {process.exitcode}"
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        if not process.is_alive():
            process.close()


def receive(channel, process, expected):
    assert channel.poll(20), f"Timed out waiting for {expected}; worker exitcode={process.exitcode}"
    try:
        message = channel.recv()
    except EOFError:
        pytest.fail(f"Worker closed its channel before {expected}; exitcode={process.exitcode}")
    if message["kind"] == "error":
        pytest.fail(message["traceback"])
    assert message["kind"] == expected, message
    return message


@pytest.fixture(params=["redis", "memcached"])
def process_case(request, relations):
    if connection.vendor == "sqlite" and connection.creation.is_in_memory_db(
        connection.settings_dict["NAME"]
    ):
        pytest.skip("Independent processes need PostgreSQL or a file-backed SQLite test database")
    variable = "NOVA_TEST_REDIS_URL" if request.param == "redis" else "NOVA_TEST_MEMCACHED_SERVER"
    address = os.environ.get(variable)
    if not address:
        pytest.skip(f"Set {variable} to run real {request.param} integration tests")
    spec = {
        "backend": request.param,
        "address": address,
        "namespace": f"nova-rel-mp:{uuid4().hex}",
        "databases": {alias: deepcopy(connections[alias].settings_dict) for alias in connections},
        # override_settings uses UserSettingsHolder, whose SETTINGS_MODULE is
        # None; pytest-django keeps the selected module in the environment.
        "settings_module": os.environ["DJANGO_SETTINGS_MODULE"],
        "installed_apps": list(settings.INSTALLED_APPS),
        "article_pk": relations.article.pk,
        "author_pk": relations.author.pk,
        "profile_pk": relations.profile.pk,
        "tag_pk": relations.tag.pk,
        "comment_pk": relations.comment.pk,
        "result_keys": set(),
    }
    with remote_backend(spec) as backend:
        try:
            yield spec, relations, backend
        finally:
            # Workers have exited before fixture teardown drops the tables.
            # Remove only this namespace's exact keys; never flush a server.
            for key in spec["result_keys"]:
                backend.delete(key)
            for model in apps.get_app_config("nova_cache_relations_tests").get_models(
                include_auto_created=True
            ):
                for database in ("*", "default"):
                    backend.delete(
                        generation_key(generation_scope(model._meta.model_name, database))
                    )


def prepare(process_case, kind, operation, rollback=False, reverse=False, pause_fill=None):
    base, case, backend = process_case
    if operation == "add":
        case.article.tags.clear()
    if operation == "create":
        case.Comment.objects.all().delete()
    spec = dict(base, kind=kind, operation=operation, rollback=rollback, reverse=reverse)
    if pause_fill:
        spec["pause_fill"] = pause_fill
    # Generate initial metadata, but leave the result for the actual reader.
    spec["key"] = QuerySetCache(backend=backend)._generate_key(related_query(spec))[0]
    spec["result_keys"].add(spec["key"])
    before = project(list(related_query(spec)), kind)
    return spec, before


def read(channel, process, spec):
    channel.send("read")
    result = receive(channel, process, "read")
    spec["result_keys"].add(result["key"])
    return result


def expected_values(spec, before):
    if spec["rollback"]:
        return before
    operation = spec["operation"]
    if operation in {"remove", "clear", "delete"}:
        return [] if spec["kind"] == "author" else [[]]
    if operation == "add":
        return [["tag-old"]]
    return ["changed"] if spec["kind"] in {"author", "profile"} else [["changed"]]


def check_committed_change(spec, before):
    with worker(spec, "reader") as (reader_channel, reader_process):
        filled = receive(reader_channel, reader_process, "filled")
        assert filled["key"] == spec["key"]
        assert filled["values"] == before
        warm = read(reader_channel, reader_process, spec)
        assert warm["values"] == before and warm["hit"] and warm["sql_count"] == 0

        with worker(spec, "writer") as (writer_channel, writer_process):
            ready = receive(writer_channel, writer_process, "ready-to-commit")
            assert len({os.getpid(), filled["pid"], ready["pid"]}) == 3
            # An actual open transaction in a different process: no callback
            # should have rotated shared generations before commit.
            uncommitted = read(reader_channel, reader_process, spec)
            assert uncommitted["key"] == spec["key"]
            assert uncommitted["values"] == before
            assert uncommitted["hit"] and uncommitted["sql_count"] == 0
            writer_channel.send("finish")
            written = receive(writer_channel, writer_process, "written")
            assert written["committed"] is not spec["rollback"]

        result = read(reader_channel, reader_process, spec)
        expected = expected_values(spec, before)
        assert result["values"] == expected, f"Committed related write left stale data: {result}"
        assert project(list(related_query(spec)), spec["kind"]) == expected
        assert result["hit"] is spec["rollback"]
        if spec["rollback"]:
            assert result["key"] == spec["key"] and result["sql_count"] == 0
        else:
            assert result["key"] != spec["key"] and result["sql_count"] > 0
        warm = read(reader_channel, reader_process, spec)
        assert warm["values"] == expected and warm["hit"] and warm["sql_count"] == 0
        reader_channel.send("stop")


@pytest.mark.parametrize(
    ("kind", "operation"),
    [
        ("author", "save"),
        ("author", "delete"),
        ("profile", "save"),
        ("tag", "save"),
        ("tag", "delete"),
        ("comment", "create"),
    ],
    ids=[
        "fk-save",
        "fk-cascade-delete",
        "nested-one-to-one",
        "prefetched-save",
        "prefetched-delete",
        "empty-reverse-create",
    ],
)
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_related_writer_with_no_cached_queries(process_case, kind, operation, rollback):
    check_committed_change(*prepare(process_case, kind, operation, rollback))


@pytest.mark.parametrize("operation", ["add", "remove", "clear"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_m2m_writer_with_no_cached_queries(process_case, operation, reverse, rollback):
    check_committed_change(*prepare(process_case, "tag", operation, rollback, reverse))


@pytest.mark.parametrize(
    ("kind", "operation", "reverse"),
    [
        ("author", "save", False),
        ("tag", "save", False),
        ("tag", "add", False),
        ("tag", "clear", True),
    ],
    ids=["fk-save", "prefetched-save", "empty-m2m-add", "reverse-m2m-clear"],
)
@pytest.mark.parametrize("phase", ["after-sql", "before-set"])
def test_late_related_fill_keeps_its_old_generation(process_case, kind, operation, reverse, phase):
    spec, before = prepare(process_case, kind, operation, reverse=reverse, pause_fill=phase)
    _base, _case, backend = process_case
    with worker(spec, "reader") as (reader_channel, reader_process):
        ready = receive(reader_channel, reader_process, "ready-to-store")
        assert ready["key"] == spec["key"]
        assert backend.get(spec["key"]) is None
        with worker(spec, "writer") as (writer_channel, writer_process):
            writing = receive(writer_channel, writer_process, "ready-to-commit")
            assert len({os.getpid(), ready["pid"], writing["pid"]}) == 3
            writer_channel.send("finish")
            assert receive(writer_channel, writer_process, "written")["committed"] is True
        reader_channel.send("resume")
        filled = receive(reader_channel, reader_process, "filled")
        assert filled["values"] == before  # The in-flight request keeps its old snapshot.
        result = read(reader_channel, reader_process, spec)
        expected = expected_values(spec, before)
        assert result["values"] == expected, f"Late fill revived stale related data: {result}"
        assert project(list(related_query(spec)), kind) == expected
        assert result["key"] != spec["key"] and not result["hit"] and result["sql_count"] > 0
        warm = read(reader_channel, reader_process, spec)
        assert warm["values"] == expected and warm["hit"] and warm["sql_count"] == 0
        reader_channel.send("stop")
    old = backend.get(spec["key"])
    if phase == "before-set":
        assert old is not None, "The delayed stale SET must really reach the cache server"
        assert project(old, kind) == before
    else:
        assert old is None, "Rotation before the final check should prevent publication"

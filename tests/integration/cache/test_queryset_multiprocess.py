"""Cross-process invalidation contract plus known-key/rollback controls."""

import multiprocessing
import os
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import connection, connections

from nova.cache.generation import generation_key, generation_scope
from nova.cache.queryset_cache import QuerySetCache
from tests.integration.cache._queryset_process_worker import remote_backend, worker_main
from tests.models import CachedItem

pytestmark = pytest.mark.integration


@contextmanager
def worker(spec, role):
    # spawn avoids inherited application state, connections and cache indices.
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
def process_case(request, transactional_db):
    if connection.vendor == "sqlite" and (
        connection.creation.is_in_memory_db(connection.settings_dict["NAME"])
    ):
        pytest.skip("Independent processes need PostgreSQL or a file-backed SQLite test database")

    variable = "NOVA_TEST_REDIS_URL" if request.param == "redis" else "NOVA_TEST_MEMCACHED_SERVER"
    address = os.environ.get(variable)
    if not address:
        pytest.skip(f"Set {variable} to run real {request.param} integration tests")

    row = CachedItem.objects.create(name="multiprocess", value=1)
    databases = {alias: deepcopy(connections[alias].settings_dict) for alias in connections}
    spec = {
        "backend": request.param,
        "address": address,
        "namespace": f"nova-mp-it:{uuid4().hex}",
        "databases": databases,
        "settings_module": settings.SETTINGS_MODULE,
        "pk": row.pk,
    }
    with remote_backend(spec) as backend:
        key = QuerySetCache(backend=backend)._generate_key(CachedItem.objects.filter(pk=row.pk))[0]
        spec["key"] = key
        try:
            yield spec
        finally:
            # No global flush: even failure cleanup touches only our one key.
            backend.delete(key)
            current = QuerySetCache(backend=backend)._generate_key(
                CachedItem.objects.filter(pk=row.pk)
            )[0]
            backend.delete(current)
            for db in ("*", "default"):
                backend.delete(generation_key(generation_scope("cacheditem", db)))


@pytest.mark.parametrize("operation", ["save", "delete"])
@pytest.mark.parametrize(
    ("rollback", "prime_writer"),
    [(False, False), (False, True), (True, False)],
    ids=["commit-unseen-key", "commit-known-key", "rollback-unseen-key"],
)
def test_writer_does_not_leave_stale_reader_cache(process_case, operation, rollback, prime_writer):
    spec = dict(process_case, operation=operation, rollback=rollback, prime_writer=prime_writer)
    with worker(spec, "reader") as (reader_channel, reader_process):
        filled = receive(reader_channel, reader_process, "filled")
        assert filled["key"] == spec["key"]
        assert filled["values"] == [1]

        with worker(spec, "writer") as (writer_channel, writer_process):
            written = receive(writer_channel, writer_process, "written")
            assert len({os.getpid(), filled["pid"], written["pid"]}) == 3
            assert written["committed"] is not rollback
        # The writer has exited and closed DB connections before we ask the
        # original reader process to read again. No sleeps determine ordering.
        reader_channel.send("read")
        result = receive(reader_channel, reader_process, "read")

    expected = [1] if rollback else ([2] if operation == "save" else [])
    assert written["database"] == expected
    assert result["database"] == expected
    assert result["values"] == expected, (
        f"A separate worker completed {operation}, rollback={rollback}, "
        f"prime_writer={prime_writer}; database={result['database']}, "
        f"cache={result['values']}, cache-read SQL count={result['sql_count']}. "
        "A writer that never cached this query must still invalidate the reader's entry."
    )
    if rollback:
        assert result["sql_count"] == 0, "Rollback should preserve a usable cache hit"


@pytest.mark.parametrize("operation", ["save", "delete"])
@pytest.mark.parametrize("phase", ["after-sql", "before-set"])
def test_overlapping_fill_cannot_republish_into_current_generation(process_case, operation, phase):
    spec = dict(
        process_case, operation=operation, rollback=False, prime_writer=False, pause_fill=phase
    )
    with worker(spec, "reader") as (reader_channel, reader_process):
        ready = receive(reader_channel, reader_process, "ready-to-store")
        assert ready["key"] == spec["key"]
        with worker(spec, "writer") as (writer_channel, writer_process):
            written = receive(writer_channel, writer_process, "written")
            assert len({os.getpid(), ready["pid"], written["pid"]}) == 3
            assert written["committed"] is True
        reader_channel.send("resume")
        filled = receive(reader_channel, reader_process, "filled")
        assert filled["values"] == [1]  # Snapshot read before the commit.
        reader_channel.send("read")
        result = receive(reader_channel, reader_process, "read")

    expected = [2] if operation == "save" else []
    assert written["database"] == expected
    assert result["database"] == expected
    assert result["values"] == expected
    with remote_backend(spec) as backend:
        old = backend.get(spec["key"])
        if phase == "before-set":
            assert [row.value for row in old] == [1], "The stale SET must really reach the server"
        else:
            assert old is None, "Rotation before the final check should prevent publication"

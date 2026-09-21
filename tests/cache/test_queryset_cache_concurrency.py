"""PostgreSQL contracts for cache fills overlapping a committed write.

Run with --ds=tests.example_project.settings_postgres. Uses separate DB
connections, a shared in-process cache, and events instead of timing sleeps.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from django.db import connection, connections, models, transaction
from django.db.models.signals import post_delete, post_save
from django.test.utils import isolate_apps

from nova.cache import invalidation
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache

WAIT_SECONDS = 10


@pytest.fixture
def race_case(transactional_db, monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent transaction visibility requires the PostgreSQL test run")

    receivers = []
    for signal in (post_save, post_delete):
        original_connect = signal.connect

        def tracked_connect(receiver, *args, _signal=signal, _connect=original_connect, **kwargs):
            receivers.append((_signal, receiver, kwargs.get("sender")))
            return _connect(receiver, *args, **kwargs)

        monkeypatch.setattr(signal, "connect", tracked_connect)

    with isolate_apps():

        class ConcurrentCacheRecord(models.Model):
            value = models.IntegerField()
            _nova_config = SimpleNamespace(cache_enabled=True)

            class Meta:
                app_label = "nova_cache_race"
                db_table = "nova_test_cache_race"

        with connection.schema_editor() as editor:
            editor.create_model(ConcurrentCacheRecord)
        cache = QuerySetCache(backend=MemoryCacheBackend(), ttl=300)
        try:
            invalidation.connect_invalidation(ConcurrentCacheRecord, cache=cache)
            row = ConcurrentCacheRecord.objects.create(value=1)
            yield ConcurrentCacheRecord, row.pk, cache
        finally:
            # The test joins the reader before removing its table or signals.
            for signal, receiver, sender in receivers:
                signal.disconnect(receiver, sender=sender)
            invalidation._CONNECTED_SIGNALS.discard((ConcurrentCacheRecord, id(cache)))
            cache.clear()
            with connection.schema_editor() as editor:
                editor.delete_model(ConcurrentCacheRecord)


@pytest.mark.parametrize(
    "delay_store",
    [False, True],
    ids=["fill-before-commit", "fill-after-commit"],
)
def test_committed_write_is_not_hidden_by_overlapping_cache_fill(race_case, delay_store):
    model, pk, cache = race_case
    read_finished = Event()
    allow_store = Event()

    class PausedQuerySet(models.QuerySet):
        def __iter__(self):
            # Django materializes the SQL result here; backend.set() has not run.
            iterator = super().__iter__()
            read_finished.set()
            if delay_store and not allow_store.wait(WAIT_SECONDS):
                raise TimeoutError("Writer did not release the reader after commit")
            return iterator

    def read_and_fill():
        # Resolve Django's connection in this worker, not in the main thread.
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SET statement_timeout = '5s'")
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SELECT pg_backend_pid()")
                reader_pid = cursor.fetchone()[0]
            qs = PausedQuerySet(model=model, using="default").filter(pk=pk)
            values = [row.value for row in cache.get_or_set(qs)]
            return reader_pid, values
        finally:
            connections.close_all()

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        writer_pid = cursor.fetchone()[0]

    assert cache.get(model.objects.filter(pk=pk)) is None
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            with transaction.atomic():
                row = model.objects.get(pk=pk)
                row.value = 2
                row.save(update_fields=["value"])
                future = executor.submit(read_and_fill)
                assert read_finished.wait(WAIT_SECONDS), "Reader did not finish its SQL query"
                if not delay_store:
                    # Reader stores value=1 while value=2 is still uncommitted.
                    reader_pid, values = future.result(timeout=WAIT_SECONDS)
                    assert reader_pid != writer_pid
                    assert values == [1]
                    query = model.objects.filter(pk=pk)

                    # The writer's transaction must bypass cached results.
                    assert cache.get(query) is None

                    # The autocommit reader really published the previously committed row.
                    # Inspect storage directly: the writer cannot read it through QuerySetCache.
                    key, _, _ = cache._generate_key(query)
                    cached = cache._state.backend.get(key)

                    assert cached is not None
                    assert [item.value for item in cached] == [1]
            # Exiting atomic commits the write and executes on_commit callbacks.
            assert model.objects.get(pk=pk).value == 2
        finally:
            # Release even on assertion failure, before executor joins its worker.
            allow_store.set()

        reader_pid, values = future.result(timeout=WAIT_SECONDS)
        assert reader_pid != writer_pid
        assert values == [1]  # The overlapping reader legitimately saw the old row.

    # A NEW reader after the completed commit must not receive that stale row.
    fresh = cache.get_or_set(model.objects.filter(pk=pk))
    assert [item.value for item in fresh] == [2], (
        "A cache fill overlapping commit published stale data after invalidation"
    )

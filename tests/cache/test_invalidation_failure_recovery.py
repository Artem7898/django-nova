"""Committed writes remain visible when the cache backend cannot delete keys."""

from types import SimpleNamespace

import pytest
from django.db import connection, models, transaction
from django.db.models.signals import post_delete, post_save
from django.test.utils import isolate_apps

from nova.cache import invalidation
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_cache_failure_recovery import FailingMemoryBackend


@pytest.fixture
def committed_cache(transactional_db, monkeypatch):
    backend = FailingMemoryBackend()
    cache = QuerySetCache(backend=backend)
    receivers = []
    for signal in (post_save, post_delete):
        original_connect = signal.connect

        def tracked_connect(receiver, *args, _signal=signal, _connect=original_connect, **kwargs):
            receivers.append((_signal, receiver, kwargs.get("sender")))
            return _connect(receiver, *args, **kwargs)

        monkeypatch.setattr(signal, "connect", tracked_connect)

    with isolate_apps():

        class RecoveryRecord(models.Model):
            value = models.IntegerField()
            _nova_config = SimpleNamespace(cache_enabled=True)

            class Meta:
                app_label = "nova_cache_recovery"
                db_table = "nova_test_cache_failure_recovery"

        with connection.schema_editor() as editor:
            editor.create_model(RecoveryRecord)
        try:
            invalidation.connect_invalidation(RecoveryRecord, cache=cache)
            yield RecoveryRecord, backend, cache
        finally:
            for signal, receiver, sender in receivers:
                signal.disconnect(receiver, sender=sender)
            invalidation._CONNECTED_SIGNALS.discard((RecoveryRecord, id(cache)))
            with connection.schema_editor() as editor:
                editor.delete_model(RecoveryRecord)


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_committed_change_is_not_hidden_by_failed_cache_delete(committed_cache, operation, caplog):
    model, backend, cache = committed_cache
    row = model.objects.create(value=1)
    pk = row.pk
    assert [item.value for item in cache.get_or_set(model.objects.filter(pk=pk))] == [1]
    key = backend.writes[-1]
    backend.fail_delete_keys.add(key)

    with transaction.atomic():
        if operation == "save":
            row.value = 2
            row.save(update_fields=["value"])
        else:
            row.delete()

    expected = [2] if operation == "save" else []
    assert list(model.objects.filter(pk=pk).values_list("value", flat=True)) == expected
    assert [item.value for item in backend.get(key)] == [1]
    assert "Failed to delete cache key" in caplog.text
    assert cache.get(model.objects.filter(pk=pk)) is None
    assert [item.value for item in cache.get_or_set(model.objects.filter(pk=pk))] == expected
    assert [item.value for item in cache.get(model.objects.filter(pk=pk))] == expected

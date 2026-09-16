"""Signal invalidation contracts using real transactions and a recording cache.

The cache boundary is mocked deliberately: these tests verify *when* and for
which database invalidation is requested, not Redis or concurrent cache reads.
"""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection, connections, models, transaction
from django.db.models.signals import post_delete, post_save
from django.test.utils import isolate_apps

from nova.cache import invalidation
from nova.cache.queryset_cache import QuerySetCache


class RequestedRollbackError(Exception):
    """Intentional rollback in transaction tests."""


@pytest.fixture
def invalidation_case(transactional_db, monkeypatch):
    receivers = []
    for signal in (post_save, post_delete):
        original_connect = signal.connect

        def tracked_connect(receiver, *args, _signal=signal, _connect=original_connect, **kwargs):
            receivers.append((_signal, receiver, kwargs.get("sender")))
            return _connect(receiver, *args, **kwargs)

        monkeypatch.setattr(signal, "connect", tracked_connect)

    with isolate_apps():

        class InvalidationRecord(models.Model):
            value = models.IntegerField(default=0)
            _nova_config = SimpleNamespace(cache_enabled=True)

            class Meta:
                app_label = "nova_invalidation_contract"
                db_table = "nova_test_invalidation_transactions"

        with connection.schema_editor() as editor:
            editor.create_model(InvalidationRecord)
        cache = Mock(spec=QuerySetCache)
        cache.invalidate_model.return_value = 1
        try:
            invalidation.connect_invalidation(InvalidationRecord, cache=cache)
            yield InvalidationRecord, cache
        finally:
            # Disconnect only handlers registered by this fixture.
            for signal, receiver, sender in receivers:
                signal.disconnect(receiver, sender=sender)
            invalidation._CONNECTED_SIGNALS.discard((InvalidationRecord, id(cache)))
            with connection.schema_editor() as editor:
                editor.delete_model(InvalidationRecord)


def prepare_change(case, operation, using="default"):
    model, cache = case
    row = model.objects.using(using).create(value=1)
    pk = row.pk
    cache.reset_mock()

    def change():
        if operation == "save":
            row.value = 2
            row.save(using=using, update_fields=["value"])
        else:
            row.delete(using=using)

    return model, cache, pk, change


def assert_persisted(model, pk, operation, using="default"):
    qs = model.objects.using(using)
    if operation == "save":
        assert qs.get(pk=pk).value == 2
    else:
        assert not qs.filter(pk=pk).exists()


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_invalidation_waits_for_commit(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    with transaction.atomic():
        change()
        cache.invalidate_model.assert_not_called()
    cache.invalidate_model.assert_called_once_with(model._meta.label_lower, "default")
    assert_persisted(model, pk, operation)


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_rollback_does_not_invalidate(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    with pytest.raises(RequestedRollbackError), transaction.atomic():
        change()
        raise RequestedRollbackError
    cache.invalidate_model.assert_not_called()
    assert model.objects.get(pk=pk).value == 1


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_savepoint_rollback_discards_callback(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    with transaction.atomic():
        with pytest.raises(RequestedRollbackError), transaction.atomic():
            change()
            raise RequestedRollbackError
        cache.invalidate_model.assert_not_called()
    cache.invalidate_model.assert_not_called()
    assert model.objects.get(pk=pk).value == 1


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_savepoint_release_waits_for_outer_commit(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    with transaction.atomic():
        with transaction.atomic():
            change()
        cache.invalidate_model.assert_not_called()
    cache.invalidate_model.assert_called_once_with(model._meta.label_lower, "default")
    assert_persisted(model, pk, operation)


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_outer_rollback_discards_released_savepoint_callback(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    with pytest.raises(RequestedRollbackError), transaction.atomic():
        with transaction.atomic():
            change()
        raise RequestedRollbackError
    cache.invalidate_model.assert_not_called()
    assert model.objects.get(pk=pk).value == 1


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_autocommit_invalidates(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    assert connection.get_autocommit()
    change()
    cache.invalidate_model.assert_called_once_with(model._meta.label_lower, "default")
    assert_persisted(model, pk, operation)


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_reconnecting_does_not_duplicate_handlers(invalidation_case, operation):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    invalidation.connect_invalidation(model, cache=cache)
    invalidation.connect_invalidation(model, cache=cache)
    with transaction.atomic():
        change()
    cache.invalidate_model.assert_called_once_with(model._meta.label_lower, "default")
    assert_persisted(model, pk, operation)


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_backend_failure_is_logged_without_reversing_commit(invalidation_case, operation, caplog):
    model, cache, pk, change = prepare_change(invalidation_case, operation)
    cache.invalidate_model.side_effect = RuntimeError("cache unavailable")
    with (
        caplog.at_level(logging.WARNING, logger="nova.cache.invalidation"),
        transaction.atomic(),
    ):
        change()
        cache.invalidate_model.assert_not_called()
    cache.invalidate_model.assert_called_once_with(model._meta.label_lower, "default")
    assert_persisted(model, pk, operation)
    failures = [r for r in caplog.records if r.name == "nova.cache.invalidation"]
    assert len(failures) == 1
    assert "Cache invalidation failed" in failures[0].getMessage()
    assert failures[0].exc_info is not None


@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_callback_uses_signal_database(invalidation_case, django_db_blocker, rollback):
    # A second connection to the SAME disposable test DB, not a production DB.
    # Keep default's transaction open to detect callbacks queued on the wrong alias.
    alias = "nova_invalidation_secondary"
    assert alias not in connections
    secondary = connection.copy(alias=alias)
    connections[alias] = secondary
    try:
        with django_db_blocker.unblock():
            model, cache, pk, change = prepare_change(invalidation_case, "save", using=alias)
            with transaction.atomic(using="default"):
                with transaction.atomic(using=alias):
                    change()
                    cache.invalidate_model.assert_not_called()
                    if rollback:
                        transaction.set_rollback(True, using=alias)
                if rollback:
                    cache.invalidate_model.assert_not_called()
                    assert model.objects.using(alias).get(pk=pk).value == 1
                else:
                    cache.invalidate_model.assert_called_once_with(model._meta.label_lower, alias)
                    assert_persisted(model, pk, "save", using=alias)
            if rollback:
                cache.invalidate_model.assert_not_called()
            else:
                cache.invalidate_model.assert_called_once_with(model._meta.label_lower, alias)
    finally:
        secondary.close()
        del connections[alias]

"""
Tests for the Read Replica Router and QuerySet integration.
"""

from __future__ import annotations

from unittest.mock import patch

from django.db import models

# We import directly from the modules, as we test the logic in isolation.
from nova.db.router import NovaDatabaseRouter, replica_state
from nova.typing.managers import NovaManager
from nova.typing.querysets import TypedQuerySet


# ---Dummy model for tests (without a real database) ---
class FakeModel(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"


class FakeQuerySet(TypedQuerySet["FakeModel"]):
    pass


class FakeManager(NovaManager["FakeModel"]):
    _queryset_class = FakeQuerySet


# --- Router State Tests ---
class TestReplicaState:
    """Tests the thread-local state manager."""

    def test_default_is_false(self) -> None:
        assert replica_state.should_use_replica() is False

    def test_set_and_clear_state(self) -> None:
        replica_state.set_read_from_replica()
        assert replica_state.should_use_replica() is True

        replica_state.clear_replica_state()
        assert replica_state.should_use_replica() is False


# --- Tests of the Django Router itself ---
class TestNovaDatabaseRouter:
    def test_db_for_read_returns_none_by_default(self) -> None:
        router = NovaDatabaseRouter()
        assert router.db_for_read(FakeModel) is None

    def test_db_for_read_returns_replica_when_active(self) -> None:
        router = NovaDatabaseRouter()
        replica_state.set_read_from_replica()

        # When the flag is raised, the router must return the alias of the replica
        # (In a real test, we would lock nova_settings.replica_db_alias)
        with patch("nova.db.router.nova_settings.replica_db_alias", "replica_db_mock"):
            result = router.db_for_read(FakeModel)
            assert result == "replica_db_mock"

    def test_db_for_write_clears_state(self) -> None:
        router = NovaDatabaseRouter()
        replica_state.set_read_from_replica()

        router.db_for_write(FakeModel, **{})

        # The record should reset the flag
        assert replica_state.should_use_replica() is False
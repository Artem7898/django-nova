"""Tests for the read-replica database router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.db import models

from nova.conf import nova_settings
from nova.db.router import (
    NovaDatabaseRouter,
    ReplicaState,
    _ReplicaLagTracker,
    replica_state,
    report_replica_lag,
)
from nova.typing.managers import NovaManager
from nova.typing.querysets import TypedQuerySet


class FakeModel(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"


class FakeQuerySet(TypedQuerySet["FakeModel"]):
    pass


class FakeManager(NovaManager["FakeModel"]):
    _queryset_class = FakeQuerySet


def test_replica_state_default_is_false() -> None:
    state = ReplicaState()

    assert state.should_use_replica() is False


def test_replica_state_set_and_clear() -> None:
    state = ReplicaState()

    state.set_read_from_replica()
    assert state.should_use_replica() is True

    state.clear_replica_state()
    assert state.should_use_replica() is False


def test_db_for_read_returns_none_by_default() -> None:
    replica_state.clear_replica_state()
    router = NovaDatabaseRouter()

    assert router.db_for_read(FakeModel) is None


def test_db_for_read_returns_replica_when_requested_and_healthy() -> None:
    router = NovaDatabaseRouter()
    replica_state.set_read_from_replica()

    try:
        with (
            patch.object(nova_settings, "replica_db_alias", "replica_db_mock"),
            patch("nova.db.router._lag_tracker") as mock_lag_tracker,
        ):
            mock_lag_tracker.is_healthy.return_value = True

            assert router.db_for_read(FakeModel) == "replica_db_mock"
            mock_lag_tracker.is_healthy.assert_called_once_with()
    finally:
        replica_state.clear_replica_state()


def test_db_for_read_falls_back_to_master_when_replica_is_unhealthy() -> None:
    router = NovaDatabaseRouter()
    replica_state.set_read_from_replica()

    try:
        with patch("nova.db.router._lag_tracker") as mock_lag_tracker:
            mock_lag_tracker.is_healthy.return_value = False

            assert router.db_for_read(FakeModel) is None
            mock_lag_tracker.is_healthy.assert_called_once_with()
    finally:
        replica_state.clear_replica_state()


def test_db_for_write_clears_replica_state_and_returns_master_fallback() -> None:
    router = NovaDatabaseRouter()
    replica_state.set_read_from_replica()

    try:
        assert router.db_for_write(FakeModel) is None
        assert replica_state.should_use_replica() is False
    finally:
        replica_state.clear_replica_state()


def test_allow_relation_returns_none() -> None:
    router = NovaDatabaseRouter()

    assert router.allow_relation(object(), FakeModel, FakeModel) is None


def test_allow_migrate_returns_none() -> None:
    router = NovaDatabaseRouter()

    assert router.allow_migrate("default", "tests", "FakeModel") is None


def test_lag_tracker_fetches_numeric_lag_from_redis() -> None:
    tracker = _ReplicaLagTracker()
    fake_client = MagicMock()
    fake_client.get.return_value = "125.5"

    with (
        patch("nova.redis.client.get_redis_client", return_value=fake_client),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 0),
    ):
        assert tracker.get_lag() == 125.5

    fake_client.get.assert_called_once_with("nova:replica_lag")


def test_lag_tracker_treats_missing_redis_value_as_zero() -> None:
    tracker = _ReplicaLagTracker()
    fake_client = MagicMock()
    fake_client.get.return_value = None

    with (
        patch("nova.redis.client.get_redis_client", return_value=fake_client),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 0),
    ):
        assert tracker.get_lag() == 0.0


def test_lag_tracker_fails_safe_to_infinity_when_redis_fails() -> None:
    tracker = _ReplicaLagTracker()

    with (
        patch(
            "nova.redis.client.get_redis_client",
            side_effect=RuntimeError("redis unavailable"),
        ),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 0),
    ):
        assert tracker.get_lag() == float("inf")


def test_lag_tracker_uses_cached_value_inside_check_interval() -> None:
    tracker = _ReplicaLagTracker()
    fake_client = MagicMock()
    fake_client.get.return_value = "100"

    with (
        patch("nova.redis.client.get_redis_client", return_value=fake_client),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 1000),
        patch("nova.db.router.time.monotonic", side_effect=[10.0, 10.5]),
    ):
        assert tracker.get_lag() == 100.0
        assert tracker.get_lag() == 100.0

    fake_client.get.assert_called_once_with("nova:replica_lag")


def test_lag_tracker_refreshes_after_check_interval() -> None:
    tracker = _ReplicaLagTracker()
    fake_client = MagicMock()
    fake_client.get.side_effect = ["100", "250"]

    with (
        patch("nova.redis.client.get_redis_client", return_value=fake_client),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 1000),
        patch("nova.db.router.time.monotonic", side_effect=[10.0, 11.1]),
    ):
        assert tracker.get_lag() == 100.0
        assert tracker.get_lag() == 250.0

    assert fake_client.get.call_count == 2


def test_lag_tracker_health_compares_lag_with_threshold() -> None:
    tracker = _ReplicaLagTracker()

    with (
        patch.object(tracker, "get_lag", return_value=499.0),
        patch.object(nova_settings, "replica_max_lag_ms", 500),
    ):
        assert tracker.is_healthy() is True

    with (
        patch.object(tracker, "get_lag", return_value=500.1),
        patch.object(nova_settings, "replica_max_lag_ms", 500),
    ):
        assert tracker.is_healthy() is False


def test_report_replica_lag_writes_value_with_minimum_ttl() -> None:
    fake_client = MagicMock()

    with (
        patch("nova.redis.client.get_redis_client", return_value=fake_client),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 100),
    ):
        report_replica_lag(123.5)

    fake_client.set.assert_called_once_with(
        "nova:replica_lag",
        "123.5",
        ex=2,
    )


def test_report_replica_lag_uses_interval_plus_one_when_larger() -> None:
    fake_client = MagicMock()

    with (
        patch("nova.redis.client.get_redis_client", return_value=fake_client),
        patch.object(nova_settings, "replica_lag_check_interval_ms", 5000),
    ):
        report_replica_lag(321.0)

    fake_client.set.assert_called_once_with(
        "nova:replica_lag",
        "321.0",
        ex=6,
    )


def test_report_replica_lag_swallows_monitoring_errors() -> None:
    fake_client = MagicMock()
    fake_client.set.side_effect = RuntimeError("redis unavailable")

    with patch("nova.redis.client.get_redis_client", return_value=fake_client):
        report_replica_lag(100.0)


def test_db_for_read_does_not_check_lag_without_replica_request() -> None:
    router = NovaDatabaseRouter()
    replica_state.clear_replica_state()

    with patch("nova.db.router._lag_tracker") as mock_lag_tracker:
        assert router.db_for_read(FakeModel) is None

    mock_lag_tracker.is_healthy.assert_not_called()

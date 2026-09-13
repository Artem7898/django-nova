"""Tests for Nova's Django system checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.core.checks import Error
from django.core.checks import Warning as DjangoWarning
from django.db import models
from pydantic import BaseModel

from nova.conf import nova_settings
from nova.core.checks import check_nova_infrastructure
from nova.typing.models import NovaModel


class CheckSchema(BaseModel):
    name: str


class CachedWithoutSchemaModel(NovaModel):
    name = models.CharField(max_length=100)

    class _Config:
        cache_enabled = True
        pydantic_schema = None

    _nova_config = _Config()

    class Meta:
        app_label = "tests"


class CachedWithSchemaModel(NovaModel):
    name = models.CharField(max_length=100)
    _nova_config = SimpleNamespace(cache_enabled=True, pydantic_schema=CheckSchema)

    class Meta:
        app_label = "tests"


class PlainModel(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"


def _app_config_for(*model_classes: type[models.Model]) -> SimpleNamespace:
    return SimpleNamespace(get_models=lambda: list(model_classes))


def test_check_warns_when_cache_enabled_without_schema() -> None:
    """W001 protects deterministic cache configuration for NovaModel."""
    issues = check_nova_infrastructure(app_configs=[_app_config_for(CachedWithoutSchemaModel)])

    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, DjangoWarning)
    assert issue.id == "nova.W001"
    assert "CachedWithoutSchemaModel" in issue.msg
    assert "pydantic_schema" in issue.hint


def test_check_does_not_warn_when_schema_is_configured() -> None:
    issues = check_nova_infrastructure(app_configs=[_app_config_for(CachedWithSchemaModel)])

    assert issues == []


def test_check_ignores_non_nova_models() -> None:
    issues = check_nova_infrastructure(app_configs=[_app_config_for(PlainModel)])

    assert issues == []


def test_check_scans_all_registered_models_when_app_configs_is_none() -> None:
    with patch("django.apps.apps.get_models", return_value=[CachedWithoutSchemaModel]):
        issues = check_nova_infrastructure()

    assert [issue.id for issue in issues] == ["nova.W001"]


def test_redis_backend_reports_unhealthy_redis() -> None:
    report = SimpleNamespace(is_healthy=False, error="connection refused")

    with (
        patch.object(nova_settings, "cache_backend", "redis"),
        patch("nova.redis.health.check_redis_health", return_value=report),
    ):
        issues = check_nova_infrastructure(app_configs=[])

    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, Error)
    assert issue.id == "nova.E001"
    assert "unreachable" in issue.msg
    assert "connection refused" in issue.hint


def test_redis_backend_has_no_issue_when_redis_is_healthy() -> None:
    report = SimpleNamespace(is_healthy=True, error=None)

    with (
        patch.object(nova_settings, "cache_backend", "redis"),
        patch("nova.redis.health.check_redis_health", return_value=report),
    ):
        issues = check_nova_infrastructure(app_configs=[])

    assert issues == []


def test_redis_backend_reports_initialization_error() -> None:
    with (
        patch.object(nova_settings, "cache_backend", "redis"),
        patch(
            "nova.redis.health.check_redis_health",
            side_effect=RuntimeError("redis package missing"),
        ),
    ):
        issues = check_nova_infrastructure(app_configs=[])

    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, Error)
    assert issue.id == "nova.E002"
    assert "redis package missing" in issue.msg
    assert "redis" in issue.hint.lower()
    assert "installed" in issue.hint.lower()


def test_non_redis_backend_skips_redis_health_check() -> None:
    with (
        patch.object(nova_settings, "cache_backend", "memory"),
        patch("nova.redis.health.check_redis_health") as mock_health,
    ):
        issues = check_nova_infrastructure(app_configs=[])

    assert issues == []
    mock_health.assert_not_called()

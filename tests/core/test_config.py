"""Tests for Pydantic-based Nova settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nova.core.config import NovaSettings


def test_defaults_are_applied() -> None:
    settings = NovaSettings(secret_key="test-secret")

    assert settings.secret_key == "test-secret"
    assert settings.debug is False
    assert settings.allowed_hosts == ["localhost"]
    assert settings.database_url == "postgres://user:pass@localhost:5432/db"
    assert settings.nova_cache_backend == "memory"
    assert settings.nova_redis_url is None
    assert settings.nova_cache_ttl == 120
    assert settings.nova_strict_validation is True
    assert settings.nova_task_worker_enabled is False


def test_secret_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        NovaSettings()


def test_settings_can_be_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "env-secret")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("ALLOWED_HOSTS", '["example.com", "localhost"]')
    monkeypatch.setenv("NOVA_CACHE_BACKEND", "redis")
    monkeypatch.setenv("NOVA_CACHE_TTL", "300")
    monkeypatch.setenv("NOVA_STRICT_VALIDATION", "false")
    monkeypatch.setenv("NOVA_TASK_WORKER_ENABLED", "true")
    monkeypatch.setenv("NOVA_REDIS_URL", "redis://localhost:6379/2")

    settings = NovaSettings()

    assert settings.secret_key == "env-secret"
    assert settings.debug is True
    assert settings.allowed_hosts == ["example.com", "localhost"]
    assert settings.nova_cache_backend == "redis"
    assert settings.nova_cache_ttl == 300
    assert settings.nova_strict_validation is False
    assert settings.nova_task_worker_enabled is True
    assert str(settings.nova_redis_url) == "redis://localhost:6379/2"


def test_extra_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "env-secret")
    monkeypatch.setenv("UNRELATED_SETTING", "ignored")

    settings = NovaSettings()

    assert settings.secret_key == "env-secret"
    assert not hasattr(settings, "unrelated_setting")


def test_cache_ttl_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        NovaSettings(secret_key="test-secret", nova_cache_ttl=0)


def test_redis_url_is_optional() -> None:
    settings = NovaSettings(secret_key="test-secret")

    assert settings.nova_redis_url is None


def test_to_django_settings_maps_core_values() -> None:
    settings = NovaSettings(
        secret_key="django-secret",
        debug=True,
        allowed_hosts=["example.com"],
        database_url="postgres://app:pass@db:5432/app",
        nova_cache_backend="redis",
        nova_cache_ttl=300,
        nova_strict_validation=False,
    )

    django_settings = settings.to_django_settings()

    assert django_settings == {
        "DEBUG": True,
        "SECRET_KEY": "django-secret",
        "ALLOWED_HOSTS": ["example.com"],
        "DATABASES": {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "URL": "postgres://app:pass@db:5432/app",
            }
        },
        "NOVA": {
            "CACHE_BACKEND": "redis",
            "CACHE_TTL": 300,
            "STRICT_VALIDATION": False,
        },
    }


def test_to_django_settings_serializes_database_url() -> None:
    settings = NovaSettings(
        secret_key="test-secret",
        database_url="postgres://user:pass@localhost:5432/nova",
    )

    assert settings.to_django_settings()["DATABASES"]["default"]["URL"] == (
        "postgres://user:pass@localhost:5432/nova"
    )

"""
Pydantic-based Django settings.
Eliminates the antipattern of accessing settings via untyped strings.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class NovaSettings(BaseSettings):
    """
    Type-safe settings. Reads from environment variables and .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Django core
    secret_key: str = Field(..., description="Django SECRET_KEY")
    debug: bool = False
    allowed_hosts: list[str] = ["localhost"]
    database_url: PostgresDsn = Field(
        default="postgres://user:pass@localhost:5432/db",
        description="PostgreSQL connection string",
    )

    # Nova specific
    nova_cache_backend: str = Field(default="memory", description="memory | redis")
    nova_redis_url: RedisDsn | None = None
    nova_cache_ttl: int = Field(default=120, ge=1)
    nova_strict_validation: bool = Field(default=True)
    nova_task_worker_enabled: bool = Field(default=False)

    def to_django_settings(self) -> dict[str, Any]:
        """Convert Nova settings to standard Django settings dict."""
        db_url = str(self.database_url)
        return {
            "DEBUG": self.debug,
            "SECRET_KEY": self.secret_key,
            "ALLOWED_HOSTS": self.allowed_hosts,
            "DATABASES": {
                "default": {
                    "ENGINE": "django.db.backends.postgresql",
                    "URL": db_url,  # Requires django-environ or custom DB backend
                }
            },
            "NOVA": {
                "CACHE_BACKEND": self.nova_cache_backend,
                "CACHE_TTL": self.nova_cache_ttl,
                "STRICT_VALIDATION": self.nova_strict_validation,
            }
        }
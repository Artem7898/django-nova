"""Django Nova configuration settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings

# ---------------------------------------------------------------------------
# Global defaults
# ---------------------------------------------------------------------------

DEFAULT_CACHE_BACKEND = "memory"
DEFAULT_CACHE_TTL = 60
DEFAULT_CACHE_MAXSIZE = 1000

NOVA_REDIS_URL = "redis://localhost:6379/0"
NOVA_REPLICA_DB_ALIAS = "replica"


def _get_setting(name: str, default: Any) -> Any:
    """Safely read a Django setting.

    During module import Django settings may not be configured yet.
    In that case Nova falls back to its own defaults.
    """
    if not settings.configured:
        return default

    return getattr(settings, name, default)


@dataclass(slots=True)
class NovaSettings:
    """Runtime configuration for Django Nova."""

    # -----------------------------------------------------------------------
    # Cache
    # -----------------------------------------------------------------------

    cache_backend: str = DEFAULT_CACHE_BACKEND
    cache_ttl: int = DEFAULT_CACHE_TTL
    cache_maxsize: int = DEFAULT_CACHE_MAXSIZE

    # -----------------------------------------------------------------------
    # Redis infrastructure
    # -----------------------------------------------------------------------

    redis_url: str = NOVA_REDIS_URL
    redis_max_connections: int = 50
    redis_socket_timeout: float = 5.0
    redis_socket_connect_timeout: float = 2.0
    redis_retry_on_timeout: bool = True
    redis_health_check_interval: int = 0

    # -----------------------------------------------------------------------
    # Replicas & lag awareness
    # -----------------------------------------------------------------------

    replica_db_alias: str = NOVA_REPLICA_DB_ALIAS
    replica_max_lag_ms: int = 500
    replica_lag_check_interval_ms: int = 1000

    # -----------------------------------------------------------------------
    # Telemetry
    # -----------------------------------------------------------------------

    tracing_enabled: bool = True
    auto_discovery: bool = True

    def __post_init__(self) -> None:
        """Load values from Django settings when available."""

        self.cache_backend = _get_setting(
            "NOVA_CACHE_BACKEND",
            self.cache_backend,
        )

        self.cache_ttl = _get_setting(
            "NOVA_CACHE_TTL",
            self.cache_ttl,
        )

        self.cache_maxsize = _get_setting(
            "NOVA_CACHE_MAXSIZE",
            self.cache_maxsize,
        )

        self.redis_url = _get_setting(
            "NOVA_REDIS_URL",
            self.redis_url,
        )

        self.redis_max_connections = _get_setting(
            "NOVA_REDIS_MAX_CONNECTIONS",
            self.redis_max_connections,
        )

        self.redis_socket_timeout = _get_setting(
            "NOVA_REDIS_SOCKET_TIMEOUT",
            self.redis_socket_timeout,
        )

        self.redis_socket_connect_timeout = _get_setting(
            "NOVA_REDIS_SOCKET_CONNECT_TIMEOUT",
            self.redis_socket_connect_timeout,
        )

        self.redis_retry_on_timeout = _get_setting(
            "NOVA_REDIS_RETRY_ON_TIMEOUT",
            self.redis_retry_on_timeout,
        )

        self.redis_health_check_interval = _get_setting(
            "NOVA_REDIS_HEALTH_CHECK_INTERVAL",
            self.redis_health_check_interval,
        )

        self.replica_db_alias = _get_setting(
            "NOVA_REPLICA_DB_ALIAS",
            self.replica_db_alias,
        )

        self.replica_max_lag_ms = _get_setting(
            "NOVA_REPLICA_MAX_LAG_MS",
            self.replica_max_lag_ms,
        )

        self.replica_lag_check_interval_ms = _get_setting(
            "NOVA_REPLICA_LAG_CHECK_INTERVAL_MS",
            self.replica_lag_check_interval_ms,
        )

        self.tracing_enabled = _get_setting(
            "NOVA_TRACING_ENABLED",
            self.tracing_enabled,
        )

        self.auto_discovery = _get_setting(
            "NOVA_AUTO_DISCOVERY",
            self.auto_discovery,
        )


# Global configuration object.
nova_settings = NovaSettings()

from dataclasses import dataclass

from django.conf import settings

# Global defaults
DEFAULT_CACHE_BACKEND = "memory"
DEFAULT_CACHE_TTL = 60
DEFAULT_CACHE_MAXSIZE = 1000

NOVA_REDIS_URL = "redis://localhost:6379/0"
NOVA_REPLICA_DB_ALIAS = "replica"


@dataclass(slots=True)
class NovaSettings:
    # --- Cache ---
    cache_backend: str = getattr(settings, "NOVA_CACHE_BACKEND", DEFAULT_CACHE_BACKEND)
    cache_ttl: int = getattr(settings, "NOVA_CACHE_TTL", DEFAULT_CACHE_TTL)

    # --- Redis Infrastructure ---
    redis_url: str = getattr(settings, "NOVA_REDIS_URL", NOVA_REDIS_URL)
    redis_max_connections: int = getattr(settings, "NOVA_REDIS_MAX_CONNECTIONS", 50)
    redis_socket_timeout: float = getattr(settings, "NOVA_REDIS_SOCKET_TIMEOUT", 5.0)
    redis_socket_connect_timeout: float = getattr(settings, "NOVA_REDIS_SOCKET_CONNECT_TIMEOUT", 2.0)
    redis_retry_on_timeout: bool = getattr(settings, "NOVA_REDIS_RETRY_ON_TIMEOUT", True)
    redis_health_check_interval: int = getattr(settings, "NOVA_REDIS_HEALTH_CHECK_INTERVAL", 0)

    # --- Replicas & Lag Awareness ---
    replica_db_alias: str = getattr(settings, "NOVA_REPLICA_DB_ALIAS", NOVA_REPLICA_DB_ALIAS)
    replica_max_lag_ms: int = getattr(settings, "NOVA_REPLICA_MAX_LAG_MS", 500)
    replica_lag_check_interval_ms: int = getattr(settings, "NOVA_REPLICA_LAG_CHECK_INTERVAL_MS", 1000)

    # --- Telemetry ---
    tracing_enabled: bool = getattr(settings, "NOVA_TRACING_ENABLED", True)
    auto_discovery: bool = getattr(settings, "NOVA_AUTO_DISCOVERY", True)


# Global singleton instance
nova_settings = NovaSettings()
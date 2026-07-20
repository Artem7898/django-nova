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
    cache_backend: str = getattr(settings, "NOVA_CACHE_BACKEND", DEFAULT_CACHE_BACKEND)
    cache_ttl: int = getattr(settings, "NOVA_CACHE_TTL", DEFAULT_CACHE_TTL)
    redis_url: str = getattr(settings, "NOVA_REDIS_URL", NOVA_REDIS_URL)

    # Настройки реплик
    replica_db_alias: str = getattr(settings, "NOVA_REPLICA_DB_ALIAS", NOVA_REPLICA_DB_ALIAS)

    tracing_enabled: bool = getattr(settings, "NOVA_TRACING_ENABLED", True)
    auto_discovery: bool = getattr(settings, "NOVA_AUTO_DISCOVERY", True)


# Global singleton instance
nova_settings = NovaSettings()
from dataclasses import dataclass

from django.conf import settings


@dataclass(slots=True)
class NovaSettings:
    cache_backend: str = getattr(
        settings,
        "NOVA_CACHE_BACKEND",
        "memory",
    )

    cache_ttl: int = getattr(
        settings,
        "NOVA_CACHE_TTL",
        120,
    )

    tracing_enabled: bool = getattr(
        settings,
        "NOVA_TRACING_ENABLED",
        True,
    )

    auto_discovery: bool = getattr(
        settings,
        "NOVA_AUTO_DISCOVERY",
        True,
    )


nova_settings = NovaSettings()
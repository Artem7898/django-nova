from dataclasses import dataclass

from django.conf import settings

DEFAULT_CACHE_BACKEND = "memory"
DEFAULT_CACHE_TTL = 60
DEFAULT_CACHE_MAXSIZE = 1000


NOVA_CACHE_REDIS_URL = (
    "redis://localhost:6379/0"
)

NOVA_CACHE_REDIS_POOL_SIZE = 100

NOVA_CACHE_REDIS_TIMEOUT = 5

# Task Backend Settings
NOVA_TASK_BACKEND: str = "asyncio"  # 'asyncio', 'redis', 'celery'
NOVA_TASK_MAX_WORKERS: int = 4
NOVA_TASK_RETRY_COUNT: int = 0
NOVA_TASK_RETRY_DELAY: float = 1.0
NOVA_TASK_DEFAULT_PRIORITY: int = 0
NOVA_TASK_QUEUE_NAME: str = "nova_tasks"
NOVA_TASK_RESULT_TTL: int = 3600


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
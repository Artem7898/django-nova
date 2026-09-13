"""Django Nova public API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.6.1"

__all__ = [
    "CacheBackend",
    "NovaConfig",
    "NovaManager",
    "NovaModel",
    "NovaSettings",
    "NovaTaskEngine",
    "SchemaRegistry",
    "TaskBackend",
    "TaskResult",
    "TypedField",
    "TypedQuerySet",
    "__version__",
    "connect_invalidation",
    "get_default_cache",
    "nova_task",
]


def __getattr__(name: str) -> Any:
    """Load public objects only when explicitly requested."""
    if name == "CacheBackend":
        from nova.cache.backends.protocol import CacheBackend

        return CacheBackend

    if name == "NovaConfig":
        from nova.typing.models import NovaConfig

        return NovaConfig

    if name == "NovaManager":
        from nova.typing.managers import NovaManager

        return NovaManager

    if name == "NovaModel":
        from nova.typing.models import NovaModel

        return NovaModel

    if name == "NovaSettings":
        from nova.conf import NovaSettings

        return NovaSettings

    if name == "NovaTaskEngine":
        from nova.tasks.engine import NovaTaskEngine

        return NovaTaskEngine

    if name == "SchemaRegistry":
        from nova.validation.schema_registry import SchemaRegistry

        return SchemaRegistry

    if name == "TaskBackend":
        from nova.tasks.backends.protocol import TaskBackend

        return TaskBackend

    if name == "TaskResult":
        from nova.tasks.models import TaskResult

        return TaskResult

    if name == "TypedField":
        from nova.typing.fields import TypedField

        return TypedField

    if name == "TypedQuerySet":
        from nova.typing.querysets import TypedQuerySet

        return TypedQuerySet

    if name == "connect_invalidation":
        from nova.cache.invalidation import connect_invalidation

        return connect_invalidation

    if name == "get_default_cache":
        from nova.cache.queryset_cache import get_default_cache

        return get_default_cache

    if name == "nova_task":
        from nova.tasks.decorators import nova_task

        return nova_task

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from nova.cache.backends.protocol import CacheBackend
    from nova.cache.invalidation import connect_invalidation
    from nova.cache.queryset_cache import get_default_cache
    from nova.conf import NovaSettings
    from nova.tasks.backends.protocol import TaskBackend
    from nova.tasks.decorators import nova_task
    from nova.tasks.engine import NovaTaskEngine
    from nova.tasks.models import TaskResult
    from nova.typing.fields import TypedField
    from nova.typing.managers import NovaManager
    from nova.typing.models import NovaConfig, NovaModel
    from nova.typing.querysets import TypedQuerySet
    from nova.validation.schema_registry import SchemaRegistry

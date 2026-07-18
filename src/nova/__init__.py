"""Django Nova public API."""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.3.0"

__all__ = [
    "CacheBackend",
    "NovaConfig",
    "NovaModel",
    "NovaSettings",
    "SchemaRegistry",
    "__version__",
    "connect_invalidation",
]


def __getattr__(name: str):
    """
   Lazy loading.
    Allows you not to load Django and Pydantic into memory if the user
    imports only __version__ or a specific submodule.
    """
    if name == "CacheBackend":
        from nova.cache.backends.protocol import CacheBackend
        return CacheBackend

    if name == "NovaModel":
        from nova.typing.models import NovaModel
        return NovaModel

    if name == "NovaConfig":
        from nova.typing.models import NovaConfig
        return NovaConfig

    if name == "NovaSettings":
        from nova.conf import NovaSettings
        return NovaSettings

    if name == "SchemaRegistry":
        from nova.validation.schema_registry import SchemaRegistry
        return SchemaRegistry

    if name == "connect_invalidation":
        from nova.cache.invalidation import connect_invalidation
        return connect_invalidation

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# This block is only needed for the static analyzer (Pyright),
# to make type hints work in the IDE (autocomplete) without actual import.
if TYPE_CHECKING:
    from nova.cache.backends.protocol import CacheBackend
    from nova.cache.invalidation import connect_invalidation
    from nova.conf import NovaSettings
    from nova.typing.models import NovaConfig, NovaModel
    from nova.validation.schema_registry import SchemaRegistry
"""
Django Nova: Next-generation Django toolkit.

Uses PEP 562 lazy imports to avoid Django's AppRegistryNotReady trap.
Top-level imports of django.db.models.Model in __init__.py are forbidden.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.1.0"
__all__ = [
    "NovaConfig",
    "NovaModel",
    "__version__",
    "connect_invalidation",
]


def __getattr__(name: str):
    """
    Lazy import mechanism.
    Triggered only when user accesses nova.NovaModel, NOT during `import nova`.
    """
    if name == "NovaModel":
        from nova.typing.models import NovaModel
        return NovaModel
    if name == "NovaConfig":
        from nova.typing.models import NovaConfig
        return NovaConfig
    if name == "connect_invalidation":
        from nova.cache.invalidation import connect_invalidation
        return connect_invalidation

    raise AttributeError(f"module 'nova' has no attribute {name}")


# This block is ONLY for type checkers (pyright/mypy).
# It is ignored at runtime because TYPE_CHECKING is False.
if TYPE_CHECKING:
    from nova.cache.invalidation import connect_invalidation
    from nova.typing.models import NovaConfig, NovaModel

"""Typed ORM: Strict type safety for Django Models and QuerySets."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "NovaConfig",
    "NovaModel",
    "TypedManager",
    "TypedQuerySet",
]

def __getattr__(name: str):
    if name == "NovaModel":
        from nova.typing.models import NovaModel
        return NovaModel
    if name == "NovaConfig":
        from nova.typing.models import NovaConfig
        return NovaConfig
    if name == "TypedQuerySet":
        from nova.typing.querysets import TypedQuerySet
        return TypedQuerySet
    if name == "TypedManager":
        from nova.typing.managers import TypedManager
        return TypedManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.typing.managers import TypedManager
    from nova.typing.models import NovaConfig, NovaModel
    from nova.typing.querysets import TypedQuerySet
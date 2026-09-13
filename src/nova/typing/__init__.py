"""Typed ORM: Strict type safety for Django Models and QuerySets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "NovaConfig",
    "NovaManager",
    "NovaModel",
    "TypedField",
    "TypedQuerySet",
]


def __getattr__(name: str) -> Any:
    """Load typed ORM objects only when explicitly requested."""
    if name == "NovaConfig":
        from nova.typing.models import NovaConfig

        return NovaConfig

    if name == "NovaManager":
        from nova.typing.managers import NovaManager

        return NovaManager

    if name == "NovaModel":
        from nova.typing.models import NovaModel

        return NovaModel

    if name == "TypedField":
        from nova.typing.fields import TypedField

        return TypedField

    if name == "TypedQuerySet":
        from nova.typing.querysets import TypedQuerySet

        return TypedQuerySet

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from nova.typing.fields import TypedField
    from nova.typing.managers import NovaManager
    from nova.typing.models import NovaConfig, NovaModel
    from nova.typing.querysets import TypedQuerySet

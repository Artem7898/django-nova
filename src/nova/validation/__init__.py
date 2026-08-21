"""Schema Registry and Pydantic Bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "SchemaRegistry",
]


def __getattr__(name: str):
    if name == "SchemaRegistry":
        from nova.validation.schema_registry import SchemaRegistry

        return SchemaRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from nova.validation.schema_registry import SchemaRegistry

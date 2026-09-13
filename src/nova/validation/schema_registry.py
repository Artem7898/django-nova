"""Registry for compiled Nova Pydantic schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class SchemaKey:
    """Unique identity of a compiled Nova schema."""

    model_cls: type[Any]
    include_relations: bool


class SchemaRegistry:
    """Process-local registry of compiled Pydantic schema variants.

    A schema is identified by both its Django model class and its relation
    projection. This prevents a scalar schema from being accidentally reused
    as a relation-enabled schema.
    """

    _schemas: dict[SchemaKey, type[BaseModel]] = {}

    @classmethod
    def register(
        cls,
        model_cls: type[Any],
        schema: type[BaseModel],
        *,
        include_relations: bool = False,
    ) -> None:
        """Register a compiled schema variant.

        Args:
            model_cls: Django model class.
            schema: Compiled Pydantic schema.
            include_relations: Whether relation fields are included.
        """
        key = SchemaKey(
            model_cls=model_cls,
            include_relations=include_relations,
        )
        cls._schemas[key] = schema

    @classmethod
    def get(
        cls,
        model_cls: type[Any],
        *,
        include_relations: bool = False,
    ) -> type[BaseModel] | None:
        """Return a compiled schema variant if available."""
        key = SchemaKey(
            model_cls=model_cls,
            include_relations=include_relations,
        )
        return cls._schemas.get(key)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered schema variants."""
        cls._schemas.clear()

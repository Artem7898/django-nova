from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SchemaRegistry:
    """
    Strictly typed registry for generated Pydantic schemas.
    Key is strictly the Django Model class.
    """
    _schemas: dict[type[Any], type[BaseModel]] = {}

    @classmethod
    def register(
        cls,
        model_cls: type[Any],
        schema: type[BaseModel],
        *,
        include_relations: bool = False,  # Kept for API compatibility
    ) -> None:
        cls._schemas[model_cls] = schema

    @classmethod
    def get(
        cls,
        model_cls: type[Any],
        *,
        include_relations: bool = False,
    ) -> type[BaseModel] | None:
        return cls._schemas.get(model_cls)

    @classmethod
    def clear(cls) -> None:
        cls._schemas.clear()
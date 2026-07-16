from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SchemaRegistry:
    _schemas: dict[
        tuple[type[Any], bool],
        type[BaseModel],
    ] = {}

    @classmethod
    def register(
        cls,
        model_cls: type[Any],
        schema: type[BaseModel],
        *,
        include_relations: bool = False,
    ) -> None:
        cls._schemas[
            (model_cls, include_relations)
        ] = schema

    @classmethod
    def get(
        cls,
        model_cls: type[Any],
        *,
        include_relations: bool = False,
    ) -> type[BaseModel] | None:
        return cls._schemas.get(
            (
                model_cls,
                include_relations,
            )
        )

    @classmethod
    def clear(cls) -> None:
        cls._schemas.clear()
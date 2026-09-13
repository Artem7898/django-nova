"""Architecture tests for schema variant isolation."""

from __future__ import annotations

from pydantic import BaseModel

from nova.validation.schema_registry import SchemaRegistry


class ScalarSchema(BaseModel):
    """Scalar schema."""

    id: int


class RelationSchema(BaseModel):
    """Relation-enabled schema."""

    id: int
    author: str


def test_relation_schema_variants_are_isolated() -> None:
    class Model:
        pass

    SchemaRegistry.clear()

    SchemaRegistry.register(
        Model,
        ScalarSchema,
        include_relations=False,
    )

    SchemaRegistry.register(
        Model,
        RelationSchema,
        include_relations=True,
    )

    assert (
        SchemaRegistry.get(
            Model,
            include_relations=False,
        )
        is ScalarSchema
    )

    assert (
        SchemaRegistry.get(
            Model,
            include_relations=True,
        )
        is RelationSchema
    )

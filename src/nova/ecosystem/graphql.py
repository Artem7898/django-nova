"""
GraphQL Schema Compiler for Strawberry.
Dynamically generates Strawberry types from Pydantic schemas.
Requires the 'strawberry-graphql' package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args, get_origin

from pydantic import BaseModel

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

# Safe Import for Strawberry
try:
    import strawberry

    STRAWBERRY_AVAILABLE = True
except ImportError:
    strawberry = None
    STRAWBERRY_AVAILABLE = False

# Mapping from Pydantic/Python types to Strawberry types
_TYPE_MAP: dict[Any, Any] = {
    str: str,
    int: int,
    float: float,
    bool: bool,
}


def to_strawberry_type(
        model_cls: type[NovaModel] | None = None,
        schema: type[BaseModel] | None = None,
) -> Any:
    """
    Dynamically compiles a Pydantic schema into a Strawberry type.

    Usage:
        ArticleGraphQLType = to_strawberry_type(Article)
        # OR
        ArticleGraphQLType = to_strawberry_type(schema=ArticleSchema)
    """
    if not STRAWBERRY_AVAILABLE:
        raise ImportError("strawberry-graphql must be installed to use GraphQL compiler")

    # Resolve the Pydantic schema
    target_schema = schema
    if target_schema is None and model_cls is not None:
        config = getattr(model_cls, '_nova_config', None)
        if not config or not config.pydantic_schema:
            raise ValueError(f"Model {model_cls.__name__} requires a pydantic_schema")
        target_schema = config.pydantic_schema

    if target_schema is None:
        raise ValueError("Either model_cls or schema must be provided")

    # Dynamically build annotations dictionary for the new class
    annotations: dict[str, Any] = {}

    for field_name, field_info in target_schema.model_fields.items():
        annotation = field_info.annotation
        origin = get_origin(annotation)

        # Handle Lists (e.g., list[TagSchema] -> list[TagGraphQLType])
        if origin is list:
            args = get_args(annotation)
            if args:
                inner_type = args[0]
                if isinstance(inner_type, type) and issubclass(inner_type, BaseModel):
                    # Recursively compile nested schema
                    nested_strawberry = to_strawberry_type(schema=inner_type)
                    annotations[field_name] = list[nested_strawberry]
                else:
                    # Primitive list (e.g., list[str])
                    annotations[field_name] = list[_TYPE_MAP.get(inner_type, str)]
            else:
                annotations[field_name] = list[Any]

        # Handle Nested Models (e.g., author: AuthorSchema -> author: AuthorGraphQLType)
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            nested_strawberry = to_strawberry_type(schema=annotation)
            annotations[field_name] = nested_strawberry

        # Handle Primitives
        else:
            annotations[field_name] = _TYPE_MAP.get(annotation, str)

    # Create the dynamic class
    type_name = f"{target_schema.__name__}GraphQL"

    dynamic_graphql_class = type(type_name, (), {})
    dynamic_graphql_class.__annotations__ = annotations

    return strawberry.type(dynamic_graphql_class, name=type_name)
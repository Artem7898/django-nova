"""
GraphQL integration for Django Nova using Strawberry.

Architecture
------------

                    Pydantic Schema
                           │
                           ▼
                  GraphQL Projection
                           │
                           ▼
                     Strawberry
                           │
                           ▼
                    GraphQL Schema

Pydantic is the canonical application/data contract.

Strawberry is only a transport/schema projection.

The adapter must preserve:
    - field names
    - scalar types
    - nullability
    - nested schema structure
    - collection structure

Unsupported types fail explicitly during compilation.

Nothing is silently converted into ``str``.
"""

from __future__ import annotations

import types
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    cast,
    get_args,
    get_origin,
)

from pydantic import BaseModel

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


# ---------------------------------------------------------------------------
# Optional dependency boundary
# ---------------------------------------------------------------------------
#
# Strawberry is optional.
#
# The dynamic import prevents an optional GraphQL dependency from becoming
# a hard dependency of Django Nova.
# ---------------------------------------------------------------------------

try:
    import strawberry as _strawberry_module
except ImportError:
    _strawberry_module: Any = None


STRAWBERRY_AVAILABLE: bool = _strawberry_module is not None


# ---------------------------------------------------------------------------
# Scalar projection
# ---------------------------------------------------------------------------
#
# The mapping is deliberately explicit.
#
# Unknown types are rejected rather than silently converted to String.
# Silent conversion would create a second implicit contract.
# ---------------------------------------------------------------------------

_SCALAR_MAP: dict[Any, Any] = {
    str: str,
    int: int,
    float: float,
    bool: bool,
}


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------


def _resolve_schema(
    model_cls: type[NovaModel] | None,
    schema: type[BaseModel] | None,
) -> type[BaseModel]:
    """
    Resolve the canonical Pydantic schema.

    Exactly one schema source is allowed.
    """
    if schema is not None:
        return schema

    if model_cls is None:
        raise ValueError("Either model_cls or schema must be provided.")

    config = getattr(model_cls, "_nova_config", None)

    if config is None:
        raise ValueError(f"Model {model_cls.__name__} requires _nova_config.")

    resolved_schema = getattr(
        config,
        "pydantic_schema",
        None,
    )

    if resolved_schema is None:
        raise ValueError(f"Model {model_cls.__name__} requires pydantic_schema in _nova_config.")

    return cast(type[BaseModel], resolved_schema)


# ---------------------------------------------------------------------------
# Annotation compiler
# ---------------------------------------------------------------------------


def _compile_annotation(
    annotation: Any,
    *,
    cache: dict[type[BaseModel], type[Any]],
) -> Any:
    """
    Project one Pydantic annotation into a Strawberry annotation.

    This function performs structural translation only.

    It never creates business validation rules.
    """
    # ------------------------------------------------------------------
    # Scalar
    # ------------------------------------------------------------------

    scalar = _SCALAR_MAP.get(annotation)

    if scalar is not None:
        return scalar

    # ------------------------------------------------------------------
    # Nested Pydantic model
    # ------------------------------------------------------------------

    if isinstance(annotation, type):
        try:
            if issubclass(annotation, BaseModel):
                return _compile_schema(
                    annotation,
                    cache=cache,
                )
        except TypeError:
            pass

    origin = get_origin(annotation)
    args = get_args(annotation)

    # ------------------------------------------------------------------
    # Optional / Union
    # ------------------------------------------------------------------

    if origin in (Union, types.UnionType):
        if not args:
            raise TypeError(f"Unsupported empty union annotation: {annotation!r}")

        has_none = type(None) in args

        non_none = tuple(argument for argument in args if argument is not type(None))

        if len(non_none) != 1:
            raise TypeError(
                f"GraphQL projection does not support multi-type unions: {annotation!r}"
            )

        inner = _compile_annotation(
            non_none[0],
            cache=cache,
        )

        # Preserve Pydantic nullability.
        if has_none:
            return inner | None

        return inner

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    if origin is list:
        if len(args) != 1:
            raise TypeError(f"List annotation requires exactly one element type: {annotation!r}")

        inner = _compile_annotation(
            args[0],
            cache=cache,
        )

        return list[inner]

    # ------------------------------------------------------------------
    # Tuple
    # ------------------------------------------------------------------

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            inner = _compile_annotation(
                args[0],
                cache=cache,
            )

            # GraphQL has list semantics rather than fixed-size tuple
            # semantics. A variadic tuple therefore projects to a list.
            return list[inner]

        raise TypeError(f"GraphQL projection does not support fixed-length tuples: {annotation!r}")

    raise TypeError(f"Unsupported Pydantic annotation for GraphQL projection: {annotation!r}")


# ---------------------------------------------------------------------------
# Recursive schema compiler
# ---------------------------------------------------------------------------


def _compile_schema(
    schema: type[BaseModel],
    *,
    cache: dict[type[BaseModel], type[Any]],
) -> type[Any]:
    """
    Compile a Pydantic model into a Strawberry GraphQL type.

    The cache protects recursive schema graphs.

    Example:

        NodeSchema
            └── child: NodeSchema
                         └── child: NodeSchema
                                      ...

    The schema is compiled only once.
    """
    if schema in cache:
        return cache[schema]

    if _strawberry_module is None:
        raise ImportError("strawberry-graphql must be installed to use to_strawberry_type().")

    type_name = f"{schema.__name__}GraphQL"

    annotations: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Recursive placeholder
    # ------------------------------------------------------------------
    #
    # Register the class before compiling nested fields.
    #
    # This prevents infinite recursion for self-referencing Pydantic
    # schemas.
    # ------------------------------------------------------------------

    dynamic_class = type(
        type_name,
        (),
        {},
    )

    cache[schema] = dynamic_class

    for field_name, field_info in schema.model_fields.items():
        annotations[field_name] = _compile_annotation(
            field_info.annotation,
            cache=cache,
        )

    dynamic_class.__annotations__ = annotations

    strawberry_type = _strawberry_module.type(
        dynamic_class,
        name=type_name,
    )

    # Replace the placeholder with the final Strawberry type.
    cache[schema] = strawberry_type

    return cast(type[Any], strawberry_type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_strawberry_type(
    model_cls: type[NovaModel] | None = None,
    schema: type[BaseModel] | None = None,
) -> type[Any]:
    """
    Compile a canonical Pydantic schema into a Strawberry GraphQL type.

    Examples
    --------

        to_strawberry_type(model_cls=Grant)

    or:

        to_strawberry_type(schema=GrantSchema)

    Pydantic remains the only application-level schema.
    """
    target_schema = _resolve_schema(
        model_cls,
        schema,
    )

    if not STRAWBERRY_AVAILABLE or _strawberry_module is None:
        raise ImportError("strawberry-graphql must be installed to use to_strawberry_type().")

    return _compile_schema(
        target_schema,
        cache={},
    )


__all__ = [
    "STRAWBERRY_AVAILABLE",
    "to_strawberry_type",
]

"""Canonical Django ↔ Pydantic serialization."""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from django.db import models
from pydantic import BaseModel


def _serialize_value(
    field: models.Field[Any, Any],
    value: Any,
) -> Any:
    """Convert a Django runtime value to a Pydantic-compatible value."""
    if value is None:
        return None

    if isinstance(field, models.FileField):
        return str(value)

    return value


def model_to_dict(
    instance: models.Model,
    *,
    schema_cls: type[BaseModel],
) -> dict[str, Any]:
    """Serialize schema-selected fields, including nested FK objects.

    Unloaded nested relations may execute synchronous database queries.
    """
    return _model_to_dict(
        instance,
        schema_cls=schema_cls,
        depth=0,
    )


def _model_to_dict(
    instance: models.Model,
    *,
    schema_cls: type[BaseModel],
    depth: int,
) -> dict[str, Any]:
    """Recursively serialize concrete fields with bounded nesting."""
    if depth >= 32:
        raise ValueError(
            "Nested serialization exceeded 32 model levels; "
            "check the schema for recursive relations."
        )

    result: dict[str, Any] = {}

    for field in instance._meta.concrete_fields:
        schema_field = schema_cls.model_fields.get(field.name)
        if schema_field is None:
            continue

        nested_schema = _get_nested_schema(schema_field.annotation)

        if field.is_relation and nested_schema is not None:
            # Access the object only when the selected schema needs it.
            related = getattr(instance, field.name)

            if related is None:
                result[field.name] = None
                continue

            if not isinstance(related, models.Model):
                raise TypeError(
                    f"{type(instance).__name__}.{field.name} must resolve to a Django model."
                )

            result[field.name] = _model_to_dict(
                related,
                schema_cls=nested_schema,
                depth=depth + 1,
            )
            continue

        value = getattr(instance, field.attname, None)
        result[field.name] = _serialize_value(field, value)

    return result


def schema_to_model(
    schema: BaseModel,
    *,
    model_cls: type[models.Model],
) -> models.Model:
    """Create a Django model from a validated Pydantic schema."""
    data = schema.model_dump(exclude_unset=True)

    valid_fields = {field.name for field in model_cls._meta.concrete_fields}

    kwargs = {name: value for name, value in data.items() if name in valid_fields}

    return model_cls(**kwargs)


def _get_nested_schema(
    annotation: Any,
) -> type[BaseModel] | None:
    """Resolve BaseModel and optional BaseModel annotations."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    if get_origin(annotation) in (Union, UnionType):
        members = [member for member in get_args(annotation) if member is not type(None)]

        # Multiple alternative schemas require explicit selection rules.
        if len(members) == 1:
            return _get_nested_schema(members[0])

    return None

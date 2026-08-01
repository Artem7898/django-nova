"""Automatic Django <-> Pydantic schema bridge."""

from __future__ import annotations

import decimal
import logging
from typing import TYPE_CHECKING, Any, cast

from django.db import models
from django.db.models.fields.related import RelatedField
from pydantic import BaseModel, ConfigDict, create_model
from pydantic import Field as PydanticField
from pydantic.fields import FieldInfo

from nova.core.exceptions import NovaValidationError
from nova.typing.models import NovaConfig  # Safe import: plain Python class, no Django Model init
from nova.validation.schema_registry import SchemaRegistry

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)

# Strict type mapping for Django -> Pydantic
_DJANGO_TO_PYDANTIC: dict[str, Any] = {
    "AutoField": int,
    "BigAutoField": int,
    "IntegerField": int,
    "BigIntegerField": int,
    "PositiveIntegerField": int,
    "PositiveSmallIntegerField": int,
    "FloatField": float,
    "DecimalField": decimal.Decimal,
    "CharField": str,
    "TextField": str,
    "EmailField": str,
    "BooleanField": bool,
    "JSONField": dict[str, Any] | list[Any] | None,
}


def _get_pydantic_type(django_field: models.Field[Any, Any]) -> Any:
    """Map a Django Field to a strictly typed Pydantic type."""
    base_type = _DJANGO_TO_PYDANTIC.get(django_field.__class__.__name__)

    if django_field.primary_key:
        return None if base_type is None else base_type | None

    if django_field.null:
        return None if base_type is None else base_type | None

    return base_type


def _extract_field_info(django_field: models.Field[Any, Any]) -> tuple[Any, FieldInfo]:
    """Extract Pydantic type and FieldInfo from a Django Field."""
    pydantic_type = _get_pydantic_type(django_field)
    field_kwargs: dict[str, Any] = {}

    if hasattr(django_field, "max_length") and django_field.max_length:
        field_kwargs["max_length"] = django_field.max_length

    if django_field.primary_key:
        field_info = PydanticField(default=None, **field_kwargs)
    elif django_field.has_default():
        field_info = PydanticField(default=django_field.get_default(), **field_kwargs)
    elif django_field.null:
        field_info = PydanticField(default=None, **field_kwargs)
    else:
        field_info = PydanticField(default=..., **field_kwargs)

    return pydantic_type, field_info


def generate_pydantic_schema(
    model_cls: type[NovaModel] | None = None, *, schema_name: str | None = None, include_relations: bool = False
) -> type[BaseModel]:
    """Generate a Pydantic schema strictly based on Django model metadata."""
    if model_cls is None:
        raise ValueError("model_cls cannot be None for schema generation")

    config = getattr(model_cls, '_nova_config', NovaConfig())
    exclude: list[str] = getattr(config, 'exclude_from_pydantic', [])

    cached = SchemaRegistry.get(model_cls)
    if cached is not None:
        return cached

    if schema_name is None:
        schema_name = f"{model_cls.__name__} (auto-generated)"

    fields_def: dict[str, tuple[Any, FieldInfo]] = {}

    for django_field in model_cls._meta.get_fields():
        if not isinstance(django_field, models.Field):
            continue

        if not hasattr(django_field, "name") or django_field.name in exclude:
            continue
        if django_field.auto_created and not django_field.concrete:
            continue
        is_relation = isinstance(django_field, RelatedField)
        if is_relation and not include_relations:
            continue

        pydantic_type, field_info = _extract_field_info(django_field)
        fields_def[django_field.name] = (pydantic_type, field_info)

    schema = create_model(
        schema_name,
        __config__=ConfigDict(
            from_attributes=True,
            extra="forbid",
        ),
        **cast(Any, fields_def),  # Pyright stub workaround for Pydantic dynamic models
    )

    SchemaRegistry.register(model_cls, schema, include_relations=include_relations)
    return schema


def model_to_pydantic(instance: NovaModel) -> BaseModel:
    """Convert Django instance to Pydantic strictly via encapsulation."""
    config = getattr(instance, '_nova_config', NovaConfig())
    schema_cls = getattr(config, 'pydantic_schema', None)

    if schema_cls is None:
        schema_cls = generate_pydantic_schema(schema_name=None, model_cls=type(instance))

    data = instance.to_dict()
    try:
        return schema_cls.model_validate(data)
    except Exception as exc:
        raise NovaValidationError(f"Failed to convert: {exc}") from exc


def pydantic_to_model(model_cls: type[NovaModel], schema: BaseModel) -> NovaModel:
    """Convert Pydantic schema back to Django instance strictly via encapsulation."""
    data = schema.model_dump(exclude_unset=True)
    kwargs = {
        f.name: data[f.name]
        for f in model_cls._meta.get_fields()
        if hasattr(f, "name") and f.name in data
    }
    return model_cls(**kwargs)
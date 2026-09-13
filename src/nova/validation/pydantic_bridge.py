"""Automatic Django ↔ Pydantic schema bridge.

The bridge is intentionally thin.

Architecture v0.7:

    Django metadata
        ↓
    Field Compiler
        ↓
    Pydantic schema
        ↓
    Canonical Serialization
        ↓
    Pydantic validation
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.db import models
from django.db.models.fields.related import RelatedField
from pydantic import BaseModel, ConfigDict, create_model
from pydantic import Field as PydanticField
from pydantic.fields import FieldInfo

from nova.core.exceptions import NovaValidationError
from nova.validation.field_compiler import compile_field
from nova.validation.schema_registry import SchemaRegistry
from nova.validation.serialization import (
    model_to_dict,
    schema_to_model,
)

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


def _extract_field_info(
    django_field: models.Field[Any, Any],
) -> tuple[Any, FieldInfo]:
    """Build a Pydantic field without evaluating callable defaults."""
    contract = compile_field(django_field)

    field_kwargs: dict[str, Any] = {}

    if contract.max_length is not None:
        field_kwargs["max_length"] = contract.max_length
    if contract.max_digits is not None:
        field_kwargs["max_digits"] = contract.max_digits

    if contract.decimal_places is not None:
        field_kwargs["decimal_places"] = contract.decimal_places

    annotation = contract.python_type

    if contract.nullable or contract.primary_key or contract.generated:
        annotation = annotation | None

    if contract.primary_key or contract.generated:
        return (
            annotation,
            PydanticField(default=None, **field_kwargs),
        )

    if contract.has_default:
        if callable(django_field.default):
            return (
                annotation,
                PydanticField(
                    default_factory=django_field.get_default,
                    **field_kwargs,
                ),
            )

        return (
            annotation,
            PydanticField(
                default=django_field.get_default(),
                **field_kwargs,
            ),
        )

    if contract.nullable:
        return (
            annotation,
            PydanticField(default=None, **field_kwargs),
        )

    return (
        annotation,
        PydanticField(default=..., **field_kwargs),
    )


def generate_pydantic_schema(
    model_cls: type[NovaModel] | None = None,
    *,
    schema_name: str | None = None,
    include_relations: bool = False,
) -> type[BaseModel]:
    """Generate a Pydantic schema from Django model metadata.

    Args:
        model_cls: Nova model class.
        schema_name: Optional generated schema name.
        include_relations: Whether relation fields should be included.

    Returns:
        Dynamically generated Pydantic model.

    Raises:
        ValueError: If model_cls is None.
    """
    if model_cls is None:
        raise ValueError("model_cls cannot be None for schema generation")

    config = getattr(model_cls, "_nova_config", None)

    exclude_values = getattr(
        config,
        "exclude_from_pydantic",
        (),
    )

    exclude: tuple[str, ...] = tuple(value for value in exclude_values if isinstance(value, str))

    cached = SchemaRegistry.get(
        model_cls,
        include_relations=include_relations,
    )

    if cached is not None:
        return cached

    if schema_name is None:
        suffix = "Relations" if include_relations else "Scalar"
        schema_name = f"{model_cls.__name__}{suffix}Schema"

    fields_def: dict[
        str,
        tuple[Any, FieldInfo],
    ] = {}

    for django_field in model_cls._meta.get_fields():
        if not isinstance(django_field, models.Field):
            continue

        if django_field.name in exclude:
            continue

        if django_field.auto_created and not django_field.concrete:
            continue

        if isinstance(django_field, RelatedField) and not include_relations:
            continue

        annotation, field_info = _extract_field_info(
            django_field,
        )

        fields_def[django_field.name] = (
            annotation,
            field_info,
        )

    schema = create_model(
        schema_name,
        __config__=ConfigDict(
            from_attributes=True,
            extra="forbid",
        ),
        **cast(Any, fields_def),
    )

    SchemaRegistry.register(
        model_cls,
        schema,
        include_relations=include_relations,
    )

    return schema


def pydantic_to_model(
    model_cls: type[NovaModel],
    schema: BaseModel,
) -> NovaModel:
    """Create a Django model instance from a validated schema."""
    try:
        return cast(
            "NovaModel",
            schema_to_model(
                schema,
                model_cls=model_cls,
            ),
        )
    except Exception as exc:
        raise NovaValidationError(
            f"Failed to convert Pydantic schema to {model_cls.__name__}: {exc}"
        ) from exc


def model_to_pydantic(
    instance: NovaModel,
) -> BaseModel:
    """Convert a Django model into its canonical Pydantic representation.

    Conversion is strictly schema-driven:

        Django instance
            ↓
        canonical serializer
            ↓
        schema whitelist
            ↓
        Pydantic model_validate()

    No model_construct() is used because it would bypass Pydantic
    validation.

    Args:
        instance: Nova model instance.

    Returns:
        Validated Pydantic model.

    Raises:
        NovaValidationError: If conversion or validation fails.
    """
    config = getattr(instance, "_nova_config", None)

    schema_cls = getattr(
        config,
        "pydantic_schema",
        None,
    )

    if schema_cls is None:
        schema_cls = generate_pydantic_schema(
            model_cls=type(instance),
            include_relations=False,
        )

    try:
        data = model_to_dict(
            instance,
            schema_cls=schema_cls,
        )

        return schema_cls.model_validate(data)
    except Exception as exc:
        raise NovaValidationError(
            f"Failed to convert {type(instance).__name__} to {schema_cls.__name__}: {exc}"
        ) from exc

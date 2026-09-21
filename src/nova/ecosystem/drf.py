"""
Django REST Framework integration for Django Nova.

Architecture
------------

                    Pydantic Schema
                           │
                           ▼
                  ┌─────────────────┐
                  │   DRF Adapter   │
                  └─────────────────┘
                           │
                           ▼
                    DRF Serializer
                           │
                           ▼
                       NovaModel

The Pydantic schema is the canonical data contract.

DRF is a transport projection only. It must never introduce independent
business validation rules.

Validation ownership:

    Pydantic
        │
        ├── business/data contract
        │
        └── projected into DRF

    NovaModel.save()
        │
        └── authoritative ORM validation boundary
"""

from __future__ import annotations

import collections.abc
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from nova.typing.django import get_model_pk

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


# ---------------------------------------------------------------------------
# Optional dependency boundary
# ---------------------------------------------------------------------------

try:
    from rest_framework import serializers as _drf_serializers
except ImportError:
    _drf_serializers: Any = None


DRF_AVAILABLE: bool = _drf_serializers is not None


def _require_drf() -> Any:
    """
    Return DRF serializers or raise a clear optional-dependency error.

    DRF remains optional for Nova core. The import happens only at the
    integration boundary.
    """
    if not DRF_AVAILABLE or _drf_serializers is None:
        raise ImportError(
            "Django REST Framework is required for DRF integration. "
            "Install the 'drf' extra, for example: "
            "uv add django-nova[drf]"
        )

    return _drf_serializers


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------


def _get_schema(model_cls: type[NovaModel]) -> type[BaseModel]:
    """
    Resolve the canonical Pydantic schema from NovaConfig.

    No schema is inferred from DRF.
    No validation rules are created here.
    """
    config = getattr(model_cls, "_nova_config", None)

    if config is None:
        raise ValueError(f"Model {model_cls.__name__} requires _nova_config.")

    schema = getattr(config, "pydantic_schema", None)

    if schema is None:
        raise ValueError(f"Model {model_cls.__name__} requires pydantic_schema in _nova_config.")

    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise ValueError(f"Model {model_cls.__name__} requires a Pydantic BaseModel schema class.")

    return schema


# ---------------------------------------------------------------------------
# Schema metadata
# ---------------------------------------------------------------------------


def _is_nested_schema(annotation: Any) -> bool:
    """
    Determine whether an annotation contains another Pydantic model.

    This function performs metadata inspection only. It does not introduce
    validation semantics.
    """
    if isinstance(annotation, type):
        try:
            if issubclass(annotation, BaseModel):
                return True
        except TypeError:
            return False

    origin = get_origin(annotation)

    if origin in (Union, UnionType):
        return any(_is_nested_schema(member) for member in get_args(annotation))

    if origin in (
        list,
        tuple,
        set,
        collections.abc.Sequence,
        collections.abc.Iterable,
        collections.abc.Container,
    ):
        args = get_args(annotation)

        if not args:
            return False

        return _is_nested_schema(args[0])

    return False


def _resolve_serializer_fields(
    model_cls: type[NovaModel],
    schema: type[BaseModel],
) -> list[str]:
    """
    Resolve serializer fields from the canonical Pydantic schema.

    Pydantic is authoritative.

    Django metadata is consulted only to ensure that projected fields
    actually exist on the persistence model.
    """
    model_fields = {
        field.name for field in model_cls._meta.get_fields() if hasattr(field, "attname")
    }

    fields = [field_name for field_name in schema.model_fields if field_name in model_fields]

    primary_key = get_model_pk(model_cls, strict=False)

    if primary_key is not None and primary_key.name not in fields:
        fields.insert(0, primary_key.name)

    return fields


# ---------------------------------------------------------------------------
# Validation bridge
# ---------------------------------------------------------------------------


def _apply_create_defaults(serializer: Any, attrs: dict[str, Any]) -> dict[str, Any]:
    """Evaluate projected scalar Django defaults once, retaining them for save.

    Updates keep omitted values from the existing instance. Relationship
    defaults continue to require an explicit serializer implementation.
    """
    if serializer.instance is not None:
        return attrs

    values = dict(attrs)
    for field in serializer.Meta.model._meta.concrete_fields:
        if (
            field.name not in values
            and field.name in serializer.fields
            and not field.is_relation
            and field.has_default()
        ):
            values[field.name] = field.get_default()
    return values


def _validation_attrs(
    serializer: Any,
    attrs: dict[str, Any],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """Adapt files and foreign keys for validation without replacing save inputs."""
    from django.db import models

    model_fields = {field.name: field for field in serializer.Meta.model._meta.concrete_fields}
    values = dict(attrs)
    for name, value in attrs.items():
        field = model_fields.get(name)
        if isinstance(field, models.FileField):
            values[name] = getattr(value, "name", value)
        elif isinstance(field, models.ForeignKey) and isinstance(value, models.Model):
            schema_field = schema.model_fields.get(name)
            if schema_field is not None and not _is_nested_schema(schema_field.annotation):
                relation = cast(Any, field)
                target_field = cast("models.Field[Any, Any]", relation.target_field)
                values[name] = getattr(value, target_field.attname)
    return values


def _build_validation_payload(
    serializer: Any,
    attrs: dict[str, Any],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """
    Build the complete Pydantic validation payload.

    For creates:
        payload = incoming attributes and evaluated scalar Django defaults

    For updates:
        payload = existing canonical state + incoming attributes

    This ensures that cross-field validators receive the complete object.
    """
    instance = getattr(serializer, "instance", None)
    values = _validation_attrs(serializer, attrs, schema)

    if instance is None:
        return values

    try:
        current_schema = instance.to_pydantic()
        payload = current_schema.model_dump()
    except Exception:
        # This is not a fallback validation implementation.
        #
        # It only reconstructs the current state. The canonical Pydantic
        # schema remains responsible for actual validation.
        payload = instance.to_dict()

    payload.update(values)

    return payload


def _translate_pydantic_errors(
    exc: PydanticValidationError,
) -> dict[str, list[str]]:
    """
    Convert Pydantic errors into DRF's transport-level representation.

    The semantic error remains a Pydantic error; this function only adapts
    it to DRF's error structure.
    """
    from rest_framework.settings import api_settings

    errors: dict[str, list[str]] = {}
    non_field_key: str = cast(Any, api_settings).NON_FIELD_ERRORS_KEY

    for error in exc.errors():
        location = error.get("loc", ())

        if not location:
            field_name = non_field_key
        else:
            first = location[0]
            field_name = non_field_key if first == "__root__" else str(first)

        message = str(error.get("msg", "Validation error"))
        errors.setdefault(field_name, []).append(message)

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_drf_serializer(model_cls: type[NovaModel]) -> type[Any]:
    """
    Compile a NovaModel into a DRF ModelSerializer.

    The generated serializer is a projection of the canonical Pydantic
    contract.

    DRF does not become another source of truth.
    NovaModel.save() remains the authoritative ORM validation boundary.

    Projected scalar Django defaults are evaluated during create validation
    and retained for save. Updates validate the complete merged state while
    writing only supplied attributes. Foreign-key instances and uploaded files
    stay intact for DRF persistence; only their validation payload is adapted.
    """
    serializers_module = _require_drf()
    schema = _get_schema(model_cls)
    serializer_fields = _resolve_serializer_fields(model_cls, schema)

    def validate(
        self: Any,
        attrs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Validate transport data through the canonical Pydantic schema.

        This provides early API feedback. Persistence remains governed by
        NovaModel.save().
        """
        attrs = _apply_create_defaults(self, attrs)
        payload = _build_validation_payload(self, attrs, schema)

        try:
            schema.model_validate(
                payload,
                from_attributes=True,
            )
        except PydanticValidationError as exc:
            raise serializers_module.ValidationError(_translate_pydantic_errors(exc)) from exc

        return attrs

    serializer_name = f"{model_cls.__name__}Serializer"

    meta_class = type(
        "Meta",
        (),
        {
            "model": model_cls,
            "fields": serializer_fields,
        },
    )

    serializer_class = type(
        serializer_name,
        (serializers_module.ModelSerializer,),
        {
            "Meta": meta_class,
            "validate": validate,
        },
    )

    return cast(type[Any], serializer_class)


__all__ = [
    "DRF_AVAILABLE",
    "to_drf_serializer",
]

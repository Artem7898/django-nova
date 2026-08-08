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
from typing import TYPE_CHECKING, Any, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


# ---------------------------------------------------------------------------
# Optional dependency boundary
# ---------------------------------------------------------------------------
#
# DRF is deliberately optional.
#
# The dependency is imported dynamically so that Nova core remains usable
# without Django REST Framework installed.
#
# Any is confined to this integration boundary and never becomes part of
# Nova's domain/core API.
# ---------------------------------------------------------------------------

try:
    import rest_framework as _drf_module
except ImportError:
    _drf_module: Any = None


DRF_AVAILABLE: bool = _drf_module is not None


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

    return cast(type[BaseModel], schema)


# ---------------------------------------------------------------------------
# Schema metadata
# ---------------------------------------------------------------------------


def _is_nested_schema(annotation: Any) -> bool:
    """
    Determine whether an annotation contains another Pydantic model.

    This is metadata inspection only.

    It does not introduce validation semantics.
    """
    if isinstance(annotation, type):
        try:
            if issubclass(annotation, BaseModel):
                return True
        except TypeError:
            return False

    origin = get_origin(annotation)

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
    Resolve DRF serializer fields from the Pydantic schema.

    Pydantic is authoritative.

    Django metadata is consulted only to prevent exposing fields that do not
    exist on the persistence model.
    """
    model_fields = {
        field.name for field in model_cls._meta.get_fields() if hasattr(field, "attname")
    }

    fields: list[str] = [
        field_name for field_name in schema.model_fields if field_name in model_fields
    ]

    # Preserve normal ModelSerializer instance semantics by including the
    # primary key when Django has one but the Pydantic contract does not.
    primary_key = model_cls._meta.pk

    if primary_key.name not in fields:
        fields.insert(0, primary_key.name)

    return fields


# ---------------------------------------------------------------------------
# Validation bridge
# ---------------------------------------------------------------------------


def _build_validation_payload(
    serializer: Any,
    attrs: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the complete Pydantic validation payload.

    For creates:
        payload = incoming attributes

    For updates:
        payload = existing canonical state + incoming attributes

    The second form is essential because cross-field Pydantic validators
    must see the complete object rather than only changed fields.
    """
    instance = getattr(serializer, "instance", None)

    if instance is None:
        return dict(attrs)

    try:
        current_schema = instance.to_pydantic()
        payload = current_schema.model_dump()
    except Exception:
        # This is not a fallback validation implementation.
        #
        # We only need a representation of the current state so that the
        # canonical Pydantic schema can perform the actual validation.
        payload = instance.to_dict()

    payload.update(attrs)

    return payload


def _translate_pydantic_errors(
    exc: PydanticValidationError,
) -> dict[str, list[str]]:
    """
    Convert Pydantic errors into DRF's error representation.

    The semantic error remains a Pydantic error.

    This function only performs transport translation.
    """
    errors: dict[str, list[str]] = {}

    for error in exc.errors():
        location = error.get("loc", ())

        if not location:
            field_name = "non_field_errors"
        else:
            first = location[0]
            field_name = "non_field_errors" if first == "__root__" else str(first)

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

    It does not become another source of truth.
    """
    if not DRF_AVAILABLE or _drf_module is None:
        raise ImportError("djangorestframework must be installed to use to_drf_serializer().")

    schema = _get_schema(model_cls)
    serializer_fields = _resolve_serializer_fields(model_cls, schema)

    serializers_module: Any = _drf_module.serializers

    def validate(
        self: Any,
        attrs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Validate transport data through the canonical Pydantic schema.

        This validation is intentionally performed at the transport
        boundary as an early feedback mechanism.

        NovaModel.save() remains the authoritative ORM validation boundary.
        """
        payload = _build_validation_payload(self, attrs)

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

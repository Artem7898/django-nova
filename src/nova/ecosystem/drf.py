"""
Django Rest Framework Auto-Serializer integration.
Dynamically generates DRF Serializers that delegate business logic validation to Pydantic.
Strict Projection: Only fields defined in the Pydantic schema (+ PK) are exposed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

# Safe Import for DRF
try:
    from pydantic import ValidationError as PydanticValidationError
    from rest_framework import serializers

    DRF_AVAILABLE = True
except ImportError:
    serializers = None
    PydanticValidationError = None
    DRF_AVAILABLE = False


def to_drf_serializer(model_cls: type[NovaModel]) -> type[serializers.ModelSerializer]:
    """
    Dynamically generates a DRF ModelSerializer for a NovaModel.
    Strictly bounds the API contract to the Pydantic schema.
    """
    if not DRF_AVAILABLE:
        raise ImportError("djangorestframework must be installed to use to_drf_serializer")

    if not model_cls._nova_config.pydantic_schema:
        raise ValueError(
            f"Model {model_cls.__name__} requires a pydantic_schema in _nova_config "
            "to generate a DRF serializer."
        )

    pydantic_schema = model_cls._nova_config.pydantic_schema
    pk_field_name = model_cls._meta.pk.name

    # ARCHITECTURE: Strict field projection.
    # We only expose fields that exist in the Pydantic schema.
    # We ALWAYS inject the PK because DRF needs it for URL lookups (detail routes).
    schema_fields = set(pydantic_schema.model_fields.keys())

    if pk_field_name not in schema_fields:
        drf_fields = [*list(schema_fields), pk_field_name]
    else:
        drf_fields = list(schema_fields)

    def pydantic_validate(self, attrs: dict) -> dict:
        """
        Intercepts DRF validation pipeline.
        `attrs` contains data already type-checked by DRF field types.
        We pass it to Pydantic to apply business-logic validators.
        """
        try:
            pydantic_schema.model_validate(attrs)
        except PydanticValidationError as exc:
            drf_errors = {}
            for err in exc.errors():
                loc = err.get("loc", ("non_field_errors",))
                field_name = loc[0] if loc and loc[0] != "__root__" else "non_field_errors"
                msg = err.get("msg", "Validation error")
                drf_errors.setdefault(field_name, []).append(msg)

            raise serializers.ValidationError(drf_errors) from exc
        except Exception as exc:
            raise serializers.ValidationError(str(exc)) from exc

        return attrs

    class Meta:
        model = model_cls
        fields = drf_fields  # STRICT PROJECTION: No "__all__"

    serializer_name = f"{model_cls.__name__}Serializer"

    return type(
        serializer_name,
        (serializers.ModelSerializer,),
        {
            "Meta": Meta,
            "validate": pydantic_validate,
        },
    )
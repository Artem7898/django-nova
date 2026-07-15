"""
Django Rest Framework Auto-Serializer integration.
Dynamically generates DRF Serializers that delegate business logic validation to Pydantic.
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

    The generated serializer uses standard DRF type checking, but intercepts
    the `validate()` step to run the model's Pydantic schema validators.
    This eliminates the need to duplicate validation logic in DRF.

    Example:
        class Article(NovaModel):
            _nova_config = NovaConfig(pydantic_schema=ArticleSchema)

        ArticleSerializer = to_drf_serializer(Article)
    """
    if not DRF_AVAILABLE:
        raise ImportError("djangorestframework must be installed to use to_drf_serializer")

    if not model_cls._nova_config.pydantic_schema:
        raise ValueError(
            f"Model {model_cls.__name__} requires a pydantic_schema in _nova_config "
            "to generate a DRF serializer."
        )

    pydantic_schema = model_cls._nova_config.pydantic_schema

    def pydantic_validate(self, attrs: dict) -> dict:
        """
        Intercepts DRF validation pipeline.
        `attrs` contains data already type-checked by DRF field types.
        We pass it to Pydantic to apply business-logic validators.
        """
        try:
            # Run Pydantic validation. We discard the result because
            # we want to keep the original Django attribute names,
            # not Pydantic aliases (if any).
            pydantic_schema.model_validate(attrs)
        except PydanticValidationError as exc:
            # Parse Pydantic error format into DRF error format
            drf_errors = {}
            for err in exc.errors():
                # err["loc"] is a tuple like ('amount',) or ('__all__',)
                loc = err.get("loc", ("non_field_errors",))
                # Extract field name, fallback to non_field_errors
                field_name = loc[0] if loc and loc[0] != "__root__" else "non_field_errors"
                msg = err.get("msg", "Validation error")
                drf_errors.setdefault(field_name, []).append(msg)

            raise serializers.ValidationError(drf_errors) from exc
        except Exception as exc:
            # Catch non-Pydantic errors (e.g., ValueError from validators)
            raise serializers.ValidationError(str(exc)) from exc

        return attrs

    class Meta:
        model = model_cls
        fields = "__all__"

    serializer_name = f"{model_cls.__name__}Serializer"

    # Dynamically construct the Serializer class
    return type(
        serializer_name,
        (serializers.ModelSerializer,),
        {
            "Meta": Meta,
            "validate": pydantic_validate,
        },
    )

"""
Django Rest Framework Auto-Serializer integration.
"""
from __future__ import annotations

import collections.abc
from typing import TYPE_CHECKING, get_args, get_origin

from pydantic import BaseModel

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

# --- ARCHITECTURE FREEZE: Pyright-Safe Import Pattern ---
try:
    from pydantic import ValidationError as PydanticValidationError
    from rest_framework import serializers
    _drf_available = True
except ImportError:
    # Fallbacks to satisfy Pyright type engine without installing DRF
    serializers = None  # type: ignore[assignment, misc]
    PydanticValidationError = Exception  # type: ignore[assignment, misc]
    _drf_available = False
# --------------------------------------------------------

def _resolve_drf_fields(model_cls: type[NovaModel], schema: type[BaseModel]) -> dict[str, str]:
    """
    Maps Pydantic schema fields to valid DRF/Django field names.
    Returns the mapping dictionary: field_name -> field__pydantic.
    """
    field_mapping = {}

    for field_name, field_info in schema.model_fields.items():
        annotation = field_info.annotation
        origin = get_origin(annotation)

        is_nested = False
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            is_nested = True
        elif origin in (list, collections.abc.Container, collections.abc.Iterable):
            args = get_args(annotation)
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                is_nested = True

        if is_nested:
            try:
                django_field = model_cls._meta.get_field(field_name)
                # If this is a foreign key, the DRF record expects a conditional 'author_id'
                target_field = getattr(django_field, 'attname', field_name)
                field_mapping[target_field] = field_name
            except Exception:
                field_mapping[field_name] = field_name
        else:
            field_mapping[field_name] = field_name

    pk_field_name = model_cls._meta.pk.name
    if pk_field_name not in field_mapping:
        field_mapping[pk_field_name] = pk_field_name

    return field_mapping


def to_drf_serializer(model_cls: type[NovaModel]) -> type[serializers.ModelSerializer]:
    """
   Architecture Freeze: using getattr instead of direct access to _nova_config
    """
    if not _drf_available:
        raise ImportError("djangorestframework must be installed to use to_drf_serializer")

    # Architecture Freeze: using getattr instead of direct access to _nova_config
    config = getattr(model_cls, '_nova_config', None)
    if not config or not getattr(config, 'pydantic_schema', None):
        raise ValueError(
            f" of the {model_cls.__name__} model requires pydantic_schema in _nova_config "
f" to generate the DRF serializer."
        )

    # Generating bidirectional mapping for name synchronization
    drf_to_pydantic_map = _resolve_drf_fields(model_cls, config.pydantic_schema)
    drf_fields = list(drf_to_pydantic_map.keys())

    def pydantic_validate(self, attrs: dict) -> dict:
        """
        Перехватывает пайплайн валидации DRF.
        Приводит структуры ID связей обратно к ожидаемым полям Pydantic.
        """
        base_payload = {}
        if getattr(self, 'partial', False) and self.instance is not None:
            for db_field in drf_fields:
                if hasattr(self.instance, db_field):
                    base_payload[db_field] = getattr(self.instance, db_field)

        base_payload.update(attrs)

        pydantic_payload = {}
        for db_field, value in base_payload.items():
            pydantic_field = drf_to_pydantic_map.get(db_field, db_field)

            # --- ARCHITECTURAL PROTECTION AGAINST TYPICAL CONFLICT ---
            pydantic_field_info = config.pydantic_schema.model_fields.get(pydantic_field)
            if pydantic_field_info:
                ann = pydantic_field_info.annotation
                is_nested_pydantic = isinstance(ann, type) and issubclass(ann, BaseModel)

                if is_nested_pydantic and isinstance(value, (int, str)):
                    continue
            # -------------------------------------------------------

            # Passing the value to Pydantic
            pydantic_payload[pydantic_field] = value

        try:
            # Launching Pydantic validation
            config.pydantic_schema.model_validate(pydantic_payload, from_attributes=True)
        except PydanticValidationError as exc:
            drf_errors = {}
            pydantic_to_drf_map = {v: k for k, v in drf_to_pydantic_map.items()}

            for err in exc.errors():
                loc = err.get("loc", ("non_field_errors",))
                pydantic_field_name = str(loc[0]) if loc and loc[0] != "__root__" else "non_field_errors"

                # Translating the error back to the name of the field that the API client expects.
                api_field_name = pydantic_to_drf_map.get(pydantic_field_name, pydantic_field_name)
                msg = err.get("msg", "Validation error")
                drf_errors.setdefault(api_field_name, []).append(msg)

            raise serializers.ValidationError(drf_errors) from exc
        except Exception as exc:
            raise serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc

        return attrs

    serializer_name = f"{model_cls.__name__}Serializer"
    meta_attrs = {"model": model_cls, "fields": drf_fields}

    return type(
        serializer_name,
        (serializers.ModelSerializer,),
        {
            "Meta": type("Meta", (), meta_attrs),
            "validate": pydantic_validate,
        },
    )


# Backward compatibility alias for tests and public API
DRF_AVAILABLE = _drf_available
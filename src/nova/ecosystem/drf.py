"""Django Rest Framework Auto-Serializer integration."""
from __future__ import annotations

import collections.abc
from typing import TYPE_CHECKING, Any, get_args, get_origin

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

# --- Canonical Pyright-Safe Import Pattern ---
try:
    import rest_framework as _drf_module
    _drf_available = True
except ImportError:
    _drf_module = None  # type: ignore[assignment, misc]
    _drf_available = False

# Assigning the MODULE to Any prevents Unknown cascades on missing package
drf: Any = _drf_module
# --------------------------------------------------------

def _resolve_drf_fields(model_cls: type[NovaModel], schema: type[BaseModel]) -> dict[str, str]:
    """Maps Pydantic schema fields to valid DRF/Django field names."""
    field_mapping: dict[str, str] = {}

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


def to_drf_serializer(model_cls: type[NovaModel]) -> Any:
    """Generates a DRF ModelSerializer bound to the Pydantic schema."""
    if not _drf_available:
        raise ImportError("djangorestframework must be installed to use to_drf_serializer")

    config = getattr(model_cls, '_nova_config', None)
    if not config:
        raise ValueError(f"Model {model_cls.__name__} requires _nova_config")

    pydantic_schema = getattr(config, 'pydantic_schema', None)
    if not pydantic_schema:
        raise ValueError(f"Model {model_cls.__name__} requires pydantic_schema in _nova_config")

    drf_to_pydantic_map = _resolve_drf_fields(model_cls, pydantic_schema)
    drf_fields = list(drf_to_pydantic_map.keys())

    def pydantic_validate(self: Any, attrs: dict[str, Any]) -> dict[str, Any]:
        """Intercepts DRF pipeline and validates via Pydantic."""
        base_payload: dict[str, Any] = {}
        if getattr(self, 'partial', False) and self.instance is not None:
            for db_field in drf_fields:
                if hasattr(self.instance, db_field):
                    base_payload[db_field] = getattr(self.instance, db_field)

        base_payload.update(attrs)

        pydantic_payload: dict[str, Any] = {}
        for db_field, value in base_payload.items():
            pydantic_field = drf_to_pydantic_map.get(db_field, db_field)

            pydantic_field_info = pydantic_schema.model_fields.get(pydantic_field)
            if pydantic_field_info:
                ann = pydantic_field_info.annotation
                is_nested_pydantic = isinstance(ann, type) and issubclass(ann, BaseModel)

                if is_nested_pydantic and isinstance(value, (int, str)):
                    continue

            pydantic_payload[pydantic_field] = value

        try:
            pydantic_schema.model_validate(pydantic_payload, from_attributes=True)
        except PydanticValidationError as exc:
            drf_errors: dict[str, list[str]] = {}
            pydantic_to_drf_map = {v: k for k, v in drf_to_pydantic_map.items()}

            # Pydantic is a hard dependency, so exc.errors() has strict typing!
            for err in exc.errors():
                loc = err.get("loc", ("non_field_errors",))
                pydantic_field_name = str(loc[0]) if loc and loc[0] != "__root__" else "non_field_errors"
                api_field_name = pydantic_to_drf_map.get(pydantic_field_name, pydantic_field_name)
                msg = err.get("msg", "Validation error")
                drf_errors.setdefault(api_field_name, []).append(msg)

            raise drf.serializers.ValidationError(drf_errors) from exc
        except Exception as exc:
            raise drf.serializers.ValidationError({"non_field_errors": [str(exc)]}) from exc

        return attrs

    serializer_name = f"{model_cls.__name__}Serializer"
    meta_attrs = {"model": model_cls, "fields": drf_fields}

    return type(
        serializer_name,
        (drf.serializers.ModelSerializer,),
        {
            "Meta": type("Meta", (), meta_attrs),
            "validate": pydantic_validate,
        },
    )


DRF_AVAILABLE = _drf_available
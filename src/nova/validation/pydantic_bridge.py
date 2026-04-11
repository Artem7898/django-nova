"""Automatic Django <-> Pydantic schema bridge."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.fields import Field
from django.db.models.fields.related import RelatedField
from pydantic import BaseModel, ConfigDict, create_model
from pydantic import Field as PydanticField
from pydantic.fields import FieldInfo

from nova.core.exceptions import NovaValidationError

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)

_DJANGO_TO_PYDANTIC: dict[str, type[Any]] = {
    "AutoField": int, "BigAutoField": int, "IntegerField": int,
    "BigIntegerField": int, "FloatField": float, "DecimalField": float,
    "CharField": str, "TextField": str, "EmailField": str,
    "BooleanField": bool, "JSONField": dict[str, Any] | list[Any] | None,
}

def _get_pydantic_type(django_field: Field[Any]) -> type[Any]:
    base_type = _DJANGO_TO_PYDANTIC.get(django_field.__class__.__name__, str)
    # Primary keys (AutoField) are always None before save
    if django_field.primary_key:
        return base_type | None
    return base_type | None if django_field.null else base_type

def _extract_field_info(django_field: Field[Any]) -> tuple[type[Any], FieldInfo]:
    pydantic_type = _get_pydantic_type(django_field)
    field_kwargs: dict[str, Any] = {}
    if hasattr(django_field, "max_length") and django_field.max_length:
        field_kwargs["max_length"] = django_field.max_length
        
    # Mirror Django semantics in Pydantic
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
    model_cls: type[NovaModel], *, schema_name: str | None = None, include_relations: bool = False
) -> type[BaseModel]:
    if schema_name is None:
        schema_name = f"{model_cls.__name__}Schema"
    fields_def: dict[str, tuple[type[Any], FieldInfo]] = {}
    exclude = model_cls._nova_config.exclude_from_pydantic
    for django_field in model_cls._meta.get_fields():
        if not hasattr(django_field, "attname") or django_field.name in exclude:
            continue
        if django_field.auto_created and not django_field.concrete:
            continue
        is_relation = isinstance(django_field, RelatedField)
        if is_relation and not include_relations:
            continue
        pydantic_type, field_info = _extract_field_info(django_field)
        fields_def[django_field.name] = (pydantic_type, field_info)
    return create_model(schema_name, __config__=ConfigDict(from_attributes=True, extra="forbid"), **fields_def)

def model_to_pydantic(instance: NovaModel) -> BaseModel:
    schema_cls = instance._nova_config.pydantic_schema
    if schema_cls is None:
        schema_cls = generate_pydantic_schema(type(instance))
    data = instance.to_dict()
    try:
        return schema_cls.model_validate(data)
    except Exception as exc:
        raise NovaValidationError(f"Failed to convert: {exc}") from exc

def pydantic_to_model(model_cls: type[NovaModel], schema: BaseModel) -> NovaModel:
    data = schema.model_dump(exclude_unset=True)
    kwargs = {f.name: data[f.name] for f in model_cls._meta.get_fields() if hasattr(f, "attname") and f.name in data}
    return model_cls(**kwargs)

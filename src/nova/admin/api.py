"""
Admin Auto-API schema generator.
Extracts UI-friendly JSON schemas directly from NovaModel Pydantic configurations.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, get_args, get_origin
from django.db.models.fields import NOT_PROVIDED
from pydantic import ValidationError as PydanticValidationError

from nova.admin.types import AdminFieldSchema, AdminSchema

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

_FRONTEND_TYPE_MAP: dict[type[Any], str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
}

GENERIC_PYDANTIC_ERRORS = {
    "Field required",
    "Input should be a valid string",
    "Input should be a valid number",
    "Input should be a valid integer",
    "Input should be a valid boolean",
}


@lru_cache(maxsize=128)
def get_admin_schema(model_cls: type[NovaModel]) -> AdminSchema:
    """Generate frontend-friendly schema from NovaModel + Pydantic schema."""
    nova_config = getattr(model_cls, "_nova_config", None)
    if not nova_config or not nova_config.pydantic_schema:
        raise ValueError(f"{model_cls.__name__} requires a pydantic_schema in _nova_config.")

    schema_cls = nova_config.pydantic_schema
    fields_def: dict[str, AdminFieldSchema] = {}

    for field_name, field_info in schema_cls.model_fields.items():
        frontend_type = _resolve_frontend_type(field_info.annotation)
        django_field = model_cls._meta.get_field(field_name)
        is_required = _is_required(field_info, django_field)
        
        field_data: AdminFieldSchema = {"type": frontend_type, "required": is_required}
        
        validation_rules = _extract_validation_rules(schema_cls, field_name, field_info.annotation)
        if validation_rules:
            field_data["validation_rules"] = validation_rules
            
        fields_def[field_name] = field_data

    return {"model": f"{model_cls._meta.app_label}.{model_cls.__name__}", "fields": fields_def}


def _build_valid_payload(schema_cls: type) -> dict[str, Any]:
    """Build minimally valid payload for triggering isolated field validation."""
    payload: dict[str, Any] = {}
    for field_name, field_info in schema_cls.model_fields.items():
        annotation = field_info.annotation
        if field_info.default is not None:
            payload[field_name] = field_info.default
            continue
        
        if annotation is str: payload[field_name] = "valid_string"
        elif annotation is int: payload[field_name] = 1
        elif annotation is float: payload[field_name] = 1.0
        elif annotation is bool: payload[field_name] = True
        else: payload[field_name] = "valid"
    return payload


def _resolve_frontend_type(annotation: Any) -> str:
    """Convert Python/Pydantic type to frontend JSON type."""
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        if args:
            annotation = args[0]
    return _FRONTEND_TYPE_MAP.get(annotation, "string")


def _is_required(field_info: Any, django_field: Any) -> bool:
    """Determine whether field is required."""
    if django_field.default is not NOT_PROVIDED:
        return False
    if django_field.null or django_field.blank:
        return False
    return field_info.is_required()


def _extract_validation_rules(schema_cls: type, field_name: str, annotation: Any) -> str | None:
    """Extract meaningful validation messages for frontend/admin UI generation."""
    core_annotation = annotation
    if hasattr(core_annotation, '__origin__'):
        args = get_args(core_annotation)
        if args:
            core_annotation = args[0]

    _BOUNDARY_DUMMIES: dict[Any, list[Any]] = {
        str: ["", None],
        int: [-1, None],
        float: [-1.0, None],
        bool: [False, None],
    }
    dummy_values = _BOUNDARY_DUMMIES.get(core_annotation, [None, "invalid_string_trigger"])

    generic_msgs: list[str] = []
    for dummy in dummy_values:
        payload = _build_valid_payload(schema_cls)
        payload[field_name] = dummy
        try:
            schema_cls.model_validate(payload)
        except PydanticValidationError as exc:
            if not exc.errors():
                continue
            relevant_msgs = [err.get("msg") for err in exc.errors() if field_name in err.get("loc", []) and err.get("msg")]
            if not relevant_msgs:
                continue
            custom_msgs = [msg for msg in relevant_msgs if msg not in GENERIC_PYDANTIC_ERRORS]
            if custom_msgs:
                return "; ".join(custom_msgs)
            generic_msgs.extend(relevant_msgs)

    if generic_msgs:
        return "; ".join(list(dict.fromkeys(generic_msgs)))
    return None

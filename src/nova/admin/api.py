"""
Admin Auto-API & Compiler.
Extracts UI-friendly JSON schemas and generates Django ModelAdmin classes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, cast, get_args, get_origin

from django.db.models.fields import NOT_PROVIDED
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from nova.admin.types import AdminFieldSchema, AdminSchema

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

    from nova.typing.models import NovaModel

# --- ARCHITECTURE FREEZE: Pyright-Safe Import Pattern ---
_admin_available: bool = True
try:
    from django import forms
    from django.contrib import admin
except ImportError:
    forms = None  # type: ignore[assignment, misc]
    admin = None  # type: ignore[assignment, misc]
    _admin_available = False

ADMIN_AVAILABLE: Final[bool] = _admin_available
# --------------------------------------------------------


_FRONTEND_TYPE_MAP: dict[type[Any], str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
}

GENERIC_PYDANTIC_ERRORS: Final[set[str]] = {
    "Field required",
    "Input should be a valid string",
    "Input should be a valid number",
    "Input should be a valid integer",
    "Input should be a valid boolean",
}


# ==========================================
# 1. JSON UI Schema Generator (Frontend)
# ==========================================

@lru_cache(maxsize=128)
def get_admin_schema(model_cls: type[NovaModel]) -> AdminSchema:
    """Generate frontend-friendly schema from NovaModel + Pydantic schema."""
    nova_config = getattr(model_cls, "_nova_config", None)
    if not nova_config or not nova_config.pydantic_schema:
        raise ValueError(f"{model_cls.__name__} requires a pydantic_schema in _nova_config.")

    schema_cls = nova_config.pydantic_schema
    fields_def: dict[str, AdminFieldSchema] = {}

    model_fields: dict[str, FieldInfo] = schema_cls.model_fields

    for field_name, field_info in model_fields.items():
        frontend_type = _resolve_frontend_type(field_info.annotation)
        django_field = model_cls._meta.get_field(field_name)
        is_required = _is_required(field_info, django_field)

        field_data: AdminFieldSchema = {"type": frontend_type, "required": is_required}

        validation_rules = _extract_validation_rules(schema_cls, field_name, field_info.annotation)
        if validation_rules:
            field_data["validation_rules"] = validation_rules

        fields_def[field_name] = field_data

    return {"model": f"{model_cls._meta.app_label}.{model_cls.__name__}", "fields": fields_def}


# ==========================================
# 2. DYNAMIC DJANGO ADMIN COMPILER (Backend)
# ==========================================

def compile_admin(model_cls: type[NovaModel]) -> type[Any]:
    """
    Generates a Django ModelAdmin that enforces Pydantic validation via Forms.
    """
    if not ADMIN_AVAILABLE or admin is None or forms is None:
        raise ImportError("Django admin must be available to compile admins.")

    # Strict Context Statement (Type Guard) for Pyright
    assert admin is not None
    assert forms is not None

    nova_config = getattr(model_cls, "_nova_config", None)
    if not nova_config or not nova_config.pydantic_schema:
        raise ValueError(f"{model_cls.__name__} requires a pydantic_schema to compile admin.")

    pydantic_schema = nova_config.pydantic_schema

    class NovaAdminForm(forms.ModelForm):  # type: ignore[misc, raw-checker]
        class Meta:
            model = model_cls
            fields = "__all__"

        def clean(self) -> dict[str, Any]:
            cleaned_data = super().clean()
            payload = cleaned_data if cleaned_data is not None else {}

            try:
                pydantic_schema.model_validate(payload)
            except PydanticValidationError as exc:
                form_errors: dict[str, list[str]] = {}

                for err in cast("list[dict[str, Any]]", exc.errors()):
                    loc = err.get("loc", ("__all__",))
                    field_name = str(loc[0]) if loc and loc[0] != "__root__" else "__all__"
                    msg = str(err.get("msg", "Validation error"))

                    form_errors.setdefault(field_name, []).append(msg)

                assert forms is not None
                raise forms.ValidationError(form_errors) from exc
            except Exception as exc:
                assert forms is not None
                raise forms.ValidationError(str(exc)) from exc

            return payload

    class NovaModelAdmin(admin.ModelAdmin):  # type: ignore[misc]
        form = NovaAdminForm

    NovaModelAdmin.__name__ = f"{model_cls.__name__}Admin"
    NovaModelAdmin.__qualname__ = f"{model_cls.__name__}Admin"

    return NovaModelAdmin


# ==========================================
# INTERNAL HELPERS
# ==========================================

def _build_valid_payload(schema_cls: type[BaseModel]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    model_fields: dict[str, FieldInfo] = schema_cls.model_fields

    for field_name, field_info in model_fields.items():
        annotation = field_info.annotation
        if field_info.default is not None:
            payload[field_name] = field_info.default
            continue
        if annotation is str:
            payload[field_name] = "valid_string"
        elif annotation is int:
            payload[field_name] = 1
        elif annotation is float:
            payload[field_name] = 1.0
        elif annotation is bool:
            payload[field_name] = True
        else:
            payload[field_name] = "valid"
    return payload


def _resolve_frontend_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        if args:
            annotation = args[0]
    return _FRONTEND_TYPE_MAP.get(annotation, "string")


def _is_required(field_info: Any, django_field: Any) -> bool:
    if django_field.default is not NOT_PROVIDED:
        return False
    if django_field.null or django_field.blank:
        return False
    return bool(getattr(field_info, "is_required", lambda: True)())


def _extract_validation_rules(schema_cls: type[BaseModel], field_name: str, annotation: Any) -> str | None:
    core_annotation = annotation
    if hasattr(core_annotation, "__origin__"):
        args = get_args(core_annotation)
        if args:
            core_annotation = args[0]

    boundary_dummies: dict[Any, list[Any]] = {
        str: ["", None],
        int: [-1, None],
        float: [-1.0, None],
        bool: [False, None],
    }
    dummy_values = boundary_dummies.get(
        core_annotation,
        [None, "invalid_string_trigger"],
    )
    generic_msgs: list[str] = []

    for dummy in dummy_values:
        payload = _build_valid_payload(schema_cls)
        payload[field_name] = dummy
        try:
            schema_cls.model_validate(payload)
        except PydanticValidationError as exc:
            errors = cast("list[dict[str, Any]]", exc.errors())
            if not errors:
                continue
            relevant_msgs = [
                str(err.get("msg"))
                for err in errors
                if field_name in err.get("loc", []) and err.get("msg")
            ]
            if not relevant_msgs:
                continue
            custom_msgs = [msg for msg in relevant_msgs if msg not in GENERIC_PYDANTIC_ERRORS]
            if custom_msgs:
                return "; ".join(custom_msgs)
            generic_msgs.extend(relevant_msgs)

    if generic_msgs:
        return "; ".join(list(dict.fromkeys(generic_msgs)))

    return None

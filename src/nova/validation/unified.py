"""
Unified validation pipeline for Django Nova.

Validation order:

Pydantic schema
    ↓
Django field validation
    ↓
Model.clean()
    ↓
Model.validate_unique()
    ↓
Model.validate_constraints()

The Pydantic schema remains the single source of truth for business
validation. Django validation provides ORM and persistence-level
correctness checks.

Infrastructure errors are intentionally not converted into validation
errors. A broken schema compiler, database connection, or infrastructure
component must remain distinguishable from invalid user data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models.fields.reverse_related import ForeignObjectRel

from nova.core.exceptions import NovaValidationError

if TYPE_CHECKING:
    from nova.typing.models import NovaConfig, NovaModel


__all__ = [
    "validate_model_instance",
]


def _get_model_config(instance: NovaModel) -> NovaConfig | None:
    """
    Return Nova configuration without coupling validation logic to a
    protected implementation detail.

    The runtime model contract intentionally exposes `_nova_config`,
    while this helper keeps all access localized to this module.
    """
    return getattr(type(instance), "_nova_config", None)


def _validate_pydantic(instance: NovaModel) -> None:
    """
    Validate the complete model state against its Pydantic schema.

    Pydantic owns business validation. The complete model state is
    validated regardless of `update_fields`, because cross-field
    invariants must always see a consistent object state.
    """
    config = _get_model_config(instance)

    if config is None:
        return

    if not config.strict_validation:
        return

    _ = instance.to_pydantic()


def _validate_django_fields(instance: NovaModel) -> None:
    """
    Run Django field-level validation.

    Reverse relations are explicitly excluded because ForeignObjectRel
    objects are relation metadata, not concrete model fields and do not
    implement the Field validation contract.
    """
    for field in instance._meta.get_fields():
        if isinstance(field, ForeignObjectRel):
            continue

        value = getattr(instance, field.attname, None)

        try:
            field.clean(value, instance)
        except DjangoValidationError as exc:
            raise NovaValidationError(
                "Django field validation failed.",
                details=_django_validation_details(exc),
            ) from exc


def _django_validation_details(
    exc: DjangoValidationError,
) -> dict[str, str]:
    """
    Convert Django ValidationError into a stable Nova error payload.

    NovaValidationError intentionally exposes string details so callers
    receive a stable, framework-independent error representation.
    """
    if hasattr(exc, "message_dict"):
        return {
            field_name: "; ".join(str(message) for message in messages)
            for field_name, messages in exc.message_dict.items()
        }

    if hasattr(exc, "messages"):
        return {
            "non_field_errors": "; ".join(
                str(message) for message in exc.messages
            )
        }

    return {
        "non_field_errors": str(exc),
    }


def _validate_model_clean(instance: NovaModel) -> None:
    """
    Execute Django's model-level clean() hook.

    This is intentionally executed after Pydantic validation. Pydantic
    remains the business contract; Django clean() is an ORM-level
    integration hook for persistence-specific validation.
    """
    try:
        instance.clean()
    except DjangoValidationError as exc:
        raise NovaValidationError(
            "Django model validation failed.",
            details=_django_validation_details(exc),
        ) from exc


def _validate_unique(instance: NovaModel) -> None:
    """
    Validate Django uniqueness constraints.

    This operation may access the database. It is therefore deliberately
    executed only after Pydantic and local field/model validation succeed.
    """
    try:
        instance.validate_unique()
    except DjangoValidationError as exc:
        raise NovaValidationError(
            "Django uniqueness validation failed.",
            details=_django_validation_details(exc),
        ) from exc


def _validate_constraints(instance: NovaModel) -> None:
    """
    Validate Django database constraints before persistence.

    Constraint validation may perform database access depending on the
    constraint definition. It therefore belongs at the end of the
    pre-save validation pipeline.
    """
    try:
        instance.validate_constraints()
    except DjangoValidationError as exc:
        raise NovaValidationError(
            "Django constraint validation failed.",
            details=_django_validation_details(exc),
        ) from exc


def validate_model_instance(instance: NovaModel) -> None:
    """
    Execute Nova's complete ORM validation pipeline.

    Pipeline:
        1. Pydantic schema validation
        2. Django field validation
        3. Django model clean()
        4. Django uniqueness validation
        5. Django constraint validation

    The pipeline is intentionally fail-fast.

    If Pydantic validation fails, no Django validation or database-backed
    validation is executed.

    If a Django validation stage fails, the error is normalized into
    NovaValidationError.

    Unexpected infrastructure errors are allowed to propagate unchanged.
    This distinction is critical for production diagnostics.
    """
    _validate_pydantic(instance)
    _validate_django_fields(instance)
    _validate_model_clean(instance)
    _validate_unique(instance)
    _validate_constraints(instance)
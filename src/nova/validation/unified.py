"""
Unified validation pipeline for Django Nova.

Pipeline:
    Pydantic -> Django fields -> Model.clean() -> Unique -> Constraints

The module deliberately keeps Django's dynamic field metadata behind
nova.typing.django. The validation pipeline itself operates only on
Nova's typed field contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError as DjangoValidationError

from nova.core.exceptions import NovaValidationError
from nova.typing.django import is_concrete_field

if TYPE_CHECKING:
    from nova.typing.models import NovaConfig, NovaModel


__all__ = ["validate_model_instance"]


def _get_model_config(
    instance: NovaModel,
) -> NovaConfig | None:
    """Return Nova configuration without coupling to _nova_config."""
    return getattr(type(instance), "_nova_config", None)


def _validate_pydantic(
    instance: NovaModel,
) -> None:
    """Validate complete model state against the Pydantic schema."""
    config = _get_model_config(instance)

    if config is None or not config.strict_validation:
        return

    _ = instance.to_pydantic()


def _validate_django_fields(
    instance: NovaModel,
) -> None:
    """
    Run Django field-level validation through Nova's typing boundary.

    Only concrete database fields participate in field-level validation.
    Reverse relations, GenericForeignKey and other virtual fields are
    excluded by is_concrete_field().
    """
    for model_field in instance._meta.get_fields():
        if not is_concrete_field(model_field):
            continue

        value = getattr(
            instance,
            model_field.attname,
            None,
        )

        try:
            model_field.clean(value, instance)
        except DjangoValidationError as exc:
            raise NovaValidationError(
                "Django field validation failed.",
                details=_django_validation_details(exc),
            ) from exc


def _django_validation_details(
    exc: DjangoValidationError,
) -> dict[str, str]:
    """Convert Django ValidationError into a stable Nova error payload."""
    if hasattr(exc, "message_dict"):
        return {
            field_name: "; ".join(str(message) for message in messages)
            for field_name, messages in exc.message_dict.items()
        }

    if hasattr(exc, "messages"):
        return {
            "non_field_errors": "; ".join(str(message) for message in exc.messages),
        }

    return {
        "non_field_errors": str(exc),
    }


def _validate_model_clean(
    instance: NovaModel,
) -> None:
    """Execute Django's model-level clean() hook."""
    try:
        instance.clean()
    except DjangoValidationError as exc:
        raise NovaValidationError(
            "Django model validation failed.",
            details=_django_validation_details(exc),
        ) from exc


def _validate_unique(
    instance: NovaModel,
) -> None:
    """Validate Django uniqueness constraints."""
    try:
        instance.validate_unique()
    except DjangoValidationError as exc:
        raise NovaValidationError(
            "Django uniqueness validation failed.",
            details=_django_validation_details(exc),
        ) from exc


def _validate_constraints(
    instance: NovaModel,
) -> None:
    """Validate Django database constraints."""
    try:
        instance.validate_constraints()
    except DjangoValidationError as exc:
        raise NovaValidationError(
            "Django constraint validation failed.",
            details=_django_validation_details(exc),
        ) from exc


def validate_model_instance(
    instance: NovaModel,
) -> None:
    """
    Execute Nova's complete ORM validation pipeline.

    Validation is intentionally fail-fast and follows this order:

        Pydantic
        -> Django fields
        -> Model.clean()
        -> Unique
        -> Constraints
    """
    _validate_pydantic(instance)
    _validate_django_fields(instance)
    _validate_model_clean(instance)
    _validate_unique(instance)
    _validate_constraints(instance)

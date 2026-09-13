"""Architecture contract tests for validation lifecycle."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from nova.validation.lifecycle import validate_lifecycle


class DemoSchema(BaseModel):
    """Minimal validation schema."""

    value: int


def _build_instance(*, value: object) -> tuple[Mock, Mock]:
    """Build a minimal Django-like model test double."""
    instance = Mock()

    concrete_field = Mock()
    concrete_field.name = "value"
    concrete_field.attname = "value"

    meta = Mock()
    meta.concrete_fields = [concrete_field]

    instance._meta = meta
    instance.value = value

    instance.clean = Mock()
    instance.validate_unique = Mock()
    instance.validate_constraints = Mock()

    return instance, concrete_field


def test_validation_lifecycle_is_strictly_ordered() -> None:
    """Django validation stages execute in the canonical order."""
    events: list[str] = []

    instance, concrete_field = _build_instance(value=10)

    concrete_field.clean.side_effect = lambda value, model: events.append("django_field")
    instance.clean.side_effect = lambda: events.append("model_clean")
    instance.validate_unique.side_effect = lambda: events.append("validate_unique")
    instance.validate_constraints.side_effect = lambda: events.append("validate_constraints")

    validate_lifecycle(
        instance,
        schema_cls=DemoSchema,
        strict=True,
    )

    assert events == [
        "django_field",
        "model_clean",
        "validate_unique",
        "validate_constraints",
    ]

    concrete_field.clean.assert_called_once_with(10, instance)
    instance.clean.assert_called_once()
    instance.validate_unique.assert_called_once()
    instance.validate_constraints.assert_called_once()


def test_pydantic_failure_stops_django_validation() -> None:
    """Pydantic validation runs before all Django validation stages."""
    instance, concrete_field = _build_instance(value="not-an-integer")

    with pytest.raises(PydanticValidationError):
        validate_lifecycle(
            instance,
            schema_cls=DemoSchema,
            strict=True,
        )

    concrete_field.clean.assert_not_called()
    instance.clean.assert_not_called()
    instance.validate_unique.assert_not_called()
    instance.validate_constraints.assert_not_called()


def test_non_strict_lifecycle_skips_pydantic_validation() -> None:
    """Non-strict validation skips the Pydantic stage."""
    instance, concrete_field = _build_instance(value="not-an-integer")

    validate_lifecycle(
        instance,
        schema_cls=DemoSchema,
        strict=False,
    )

    concrete_field.clean.assert_called_once_with(
        "not-an-integer",
        instance,
    )
    instance.clean.assert_called_once()
    instance.validate_unique.assert_called_once()
    instance.validate_constraints.assert_called_once()

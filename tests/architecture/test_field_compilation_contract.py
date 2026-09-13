"""Contracts for Django field compilation into Pydantic fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.db import models
from pydantic import ValidationError, create_model

from nova.validation.pydantic_bridge import _extract_field_info


class CustomCharField(models.CharField):
    """Must inherit the CharField mapping through MRO."""


class UnknownField(models.Field):
    """A field without a specialized Python type mapping."""


def make_schema(field: models.Field[Any, Any]):
    annotation, field_info = _extract_field_info(field)
    return create_model(
        "FieldContractSchema",
        value=(annotation, field_info),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (models.SlugField(), "django-nova"),
        (
            models.DateTimeField(),
            datetime(2026, 9, 13, 12, 0),
        ),
        (models.FileField(), "documents/report.pdf"),
        (models.ImageField(), "images/logo.png"),
        (CustomCharField(max_length=30), "custom"),
    ],
    ids=["slug", "datetime", "file", "image", "mro"],
)
def test_known_field_preserves_value(field, value) -> None:
    schema = make_schema(field)

    result = schema.model_validate({"value": value})

    assert result.value == value
    assert type(result.value) is type(value)


def test_unknown_field_falls_back_to_any() -> None:
    schema = make_schema(UnknownField())

    assert schema.model_fields["value"].annotation is Any
    assert schema.model_fields["value"].is_required()

    payload = {"nested": [1, "two", True]}
    assert schema.model_validate({"value": payload}).value == payload


def test_nullable_field_accepts_none_and_omission() -> None:
    schema = make_schema(
        models.CharField(max_length=30, null=True),
    )

    assert schema.model_validate({"value": None}).value is None
    assert schema.model_validate({}).value is None


def test_nonnullable_field_is_required_and_rejects_none() -> None:
    schema = make_schema(
        models.CharField(max_length=30),
    )

    with pytest.raises(ValidationError) as missing:
        schema.model_validate({})

    assert missing.value.errors()[0]["type"] == "missing"

    with pytest.raises(ValidationError):
        schema.model_validate({"value": None})


@pytest.mark.parametrize("option", ["auto_now", "auto_now_add"])
def test_generated_datetime_can_be_omitted(option: str) -> None:
    field = (
        models.DateTimeField(auto_now=True)
        if option == "auto_now"
        else models.DateTimeField(auto_now_add=True)
    )
    schema = make_schema(field)

    assert not schema.model_fields["value"].is_required()
    assert schema.model_validate({}).value is None


def test_callable_default_runs_per_instance() -> None:
    generated: list[UUID] = []

    def new_uuid() -> UUID:
        value = uuid4()
        generated.append(value)
        return value

    schema = make_schema(
        models.UUIDField(default=new_uuid),
    )

    # Compiling a schema must not execute a model default.
    assert generated == []

    first = schema.model_validate({})
    second = schema.model_validate({})

    assert len(generated) == 2
    assert first.value == generated[0]
    assert second.value == generated[1]
    assert first.value != second.value

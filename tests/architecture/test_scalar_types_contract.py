"""Contracts for Decimal and UUID field compilation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from django.db import models
from pydantic import ValidationError, create_model

from nova.validation.pydantic_bridge import _extract_field_info


@pytest.mark.parametrize(
    ("field", "expected_type", "raw_value", "expected_value"),
    [
        (
            models.DecimalField(max_digits=10, decimal_places=2),
            Decimal,
            "1234.56",
            Decimal("1234.56"),
        ),
        (
            models.UUIDField(),
            UUID,
            "12345678-1234-5678-1234-567812345678",
            UUID("12345678-1234-5678-1234-567812345678"),
        ),
    ],
    ids=["decimal", "uuid"],
)
def test_scalar_type_and_conversion(
    field: models.Field[Any, Any],
    expected_type: type,
    raw_value: str,
    expected_value: object,
) -> None:
    annotation, field_info = _extract_field_info(field)

    assert annotation is expected_type

    schema = create_model(
        "ScalarSchema",
        value=(annotation, field_info),
    )
    result = schema.model_validate({"value": raw_value})

    assert type(result.value) is expected_type
    assert result.value == expected_value


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        (
            models.DecimalField(max_digits=10, decimal_places=2),
            "not-a-decimal",
        ),
        (
            models.UUIDField(),
            "not-a-uuid",
        ),
    ],
    ids=["decimal", "uuid"],
)
def test_invalid_scalar_is_rejected(
    field: models.Field[Any, Any],
    invalid_value: str,
) -> None:
    annotation, field_info = _extract_field_info(field)
    schema = create_model(
        "InvalidScalarSchema",
        value=(annotation, field_info),
    )

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate({"value": invalid_value})

    assert exc_info.value.errors()[0]["loc"] == ("value",)


@pytest.mark.parametrize(
    "field",
    [
        models.DecimalField(
            max_digits=10,
            decimal_places=2,
            null=True,
        ),
        models.UUIDField(null=True),
    ],
    ids=["decimal", "uuid"],
)
def test_nullable_scalar_accepts_none(
    field: models.Field[Any, Any],
) -> None:
    annotation, field_info = _extract_field_info(field)
    schema = create_model(
        "NullableScalarSchema",
        value=(annotation, field_info),
    )

    assert schema.model_validate({"value": None}).value is None
    assert schema.model_validate({}).value is None


def test_text_max_length_is_preserved() -> None:
    annotation, field_info = _extract_field_info(
        models.CharField(max_length=3),
    )
    schema = create_model(
        "LimitedTextSchema",
        value=(annotation, field_info),
    )

    assert schema.model_validate({"value": "abc"}).value == "abc"

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate({"value": "abcd"})

    assert exc_info.value.errors()[0]["type"] == "string_too_long"

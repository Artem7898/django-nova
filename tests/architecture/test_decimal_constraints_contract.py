"""Contracts for generated Decimal field constraints."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import models
from pydantic import BaseModel, ValidationError, create_model

from nova.validation.pydantic_bridge import _extract_field_info


def make_decimal_schema(
    *,
    max_digits: int = 5,
    decimal_places: int = 2,
    nullable: bool = False,
) -> type[BaseModel]:
    annotation, field_info = _extract_field_info(
        models.DecimalField(
            max_digits=max_digits,
            decimal_places=decimal_places,
            null=nullable,
        ),
    )

    return create_model(
        "DecimalConstraintSchema",
        amount=(annotation, field_info),
    )


@pytest.mark.parametrize(
    "value",
    ["0", "0.01", "999.99", "-999.99"],
)
def test_valid_decimal_is_preserved(value: str) -> None:
    schema = make_decimal_schema()

    result = schema.model_validate({"amount": Decimal(value)})

    assert result.model_dump()["amount"] == Decimal(value)


@pytest.mark.parametrize(
    "value",
    [
        "1234.56",  # More than five total digits.
        "0.001",  # More than two fractional digits.
        "1000",  # More than three whole digits.
    ],
)
def test_decimal_exceeding_limits_is_rejected(value: str) -> None:
    schema = make_decimal_schema()

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate({"amount": Decimal(value)})

    assert exc_info.value.errors()[0]["loc"] == ("amount",)


def test_zero_decimal_places_is_enforced() -> None:
    schema = make_decimal_schema(
        max_digits=3,
        decimal_places=0,
    )

    result = schema.model_validate({"amount": Decimal("999")})
    assert result.model_dump()["amount"] == Decimal("999")

    with pytest.raises(ValidationError):
        schema.model_validate({"amount": Decimal("0.1")})


def test_nullable_decimal_keeps_constraints() -> None:
    schema = make_decimal_schema(nullable=True)

    assert schema.model_validate({"amount": None}).model_dump() == {
        "amount": None,
    }
    assert schema.model_validate({}).model_dump() == {
        "amount": None,
    }

    with pytest.raises(ValidationError):
        schema.model_validate({"amount": Decimal("0.001")})

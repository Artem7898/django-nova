"""Compile Django fields into Nova/Pydantic field contracts."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import models


@dataclass(frozen=True, slots=True)
class FieldContract:
    """Canonical semantic contract for a Django model field."""

    python_type: Any
    max_length: int | None
    required: bool
    nullable: bool
    primary_key: bool
    generated: bool
    has_default: bool
    file_like: bool
    max_digits: int | None = None
    decimal_places: int | None = None


_DJANGO_TYPE_MAP: dict[type[models.Field[Any, Any]], Any] = {
    models.AutoField: int,
    models.BigAutoField: int,
    models.IntegerField: int,
    models.BigIntegerField: int,
    models.PositiveIntegerField: int,
    models.PositiveSmallIntegerField: int,
    models.FloatField: float,
    models.DecimalField: Decimal,
    models.UUIDField: UUID,
    models.CharField: str,
    models.TextField: str,
    models.EmailField: str,
    models.SlugField: str,
    models.BooleanField: bool,
    models.DateTimeField: datetime.datetime,
    models.DateField: datetime.date,
    models.TimeField: datetime.time,
    models.JSONField: dict[str, Any] | list[Any] | None,
    models.FileField: str,
    models.ImageField: str,
}


def get_pydantic_type(
    field: models.Field[Any, Any],
) -> Any:
    """Return the first registered Pydantic type from the field MRO."""
    for field_type in type(field).__mro__:
        pydantic_type = _DJANGO_TYPE_MAP.get(field_type)
        if pydantic_type is not None:
            return pydantic_type

    return Any


def compile_field(
    field: models.Field[Any, Any],
) -> FieldContract:
    """Compile a Django field into a canonical Nova field contract."""
    file_like = isinstance(field, models.FileField)

    generated = bool(getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False))

    primary_key = bool(field.primary_key)
    nullable = bool(field.null)
    has_default = field.default is not models.NOT_PROVIDED

    required = not (primary_key or generated or nullable or has_default)

    # Transfer length constraints only for supported text fields.
    # UUIDField.max_length describes storage, not the Python UUID value.
    max_length: int | None = None
    if isinstance(field, (models.CharField, models.TextField)):
        raw_max_length = field.max_length
        if isinstance(raw_max_length, int):
            max_length = raw_max_length

    max_digits: int | None = None
    decimal_places: int | None = None

    if isinstance(field, models.DecimalField):
        max_digits = field.max_digits
        decimal_places = field.decimal_places

    return FieldContract(
        python_type=get_pydantic_type(field),
        max_length=max_length,
        required=required,
        nullable=nullable,
        primary_key=primary_key,
        generated=generated,
        has_default=has_default,
        file_like=file_like,
        max_digits=max_digits,
        decimal_places=decimal_places,
    )

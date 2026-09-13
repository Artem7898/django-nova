"""Canonical Nova model validation lifecycle."""

from __future__ import annotations

from django.db import models
from pydantic import BaseModel

from nova.validation.serialization import model_to_dict


def validate_lifecycle(
    instance: models.Model,
    *,
    schema_cls: type[BaseModel] | None = None,
    strict: bool = True,
) -> None:
    """Run Nova's canonical validation lifecycle."""
    if strict and schema_cls is not None:
        data = model_to_dict(
            instance,
            schema_cls=schema_cls,
        )
        schema_cls.model_validate(data)

    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname, None)
        field.clean(value, instance)

    instance.clean()

    instance.validate_unique()

    instance.validate_constraints()

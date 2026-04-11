"""
Unified validation pipeline.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError as DjangoValidationError

from nova.core.exceptions import NovaValidationError

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)


def validate_model_instance(instance: NovaModel) -> None:
    config = instance._nova_config

    # Stage 1: Pydantic schema validation
    if config.pydantic_schema is not None:
        try:
            instance.to_pydantic()
        except Exception as exc:
            if config.strict_validation:
                raise NovaValidationError(
                    f"Pydantic validation failed: {exc}",
                    details={"model": type(instance).__name__}
                ) from exc
            logger.warning("Pydantic validation failed (non-strict): %s", exc)

    # Stage 2: Django field constraints
    errors: dict[str, str] = {}
    for field in instance._meta.get_fields():
        if hasattr(field, 'clean'):
            try:
                field.clean(getattr(instance, field.attname, None), instance)
            except DjangoValidationError as e:
                errors[field.name] = str(e)

    if errors and config.strict_validation:
        raise NovaValidationError(f"Field validation failed: {errors}")

    # Stage 3: Business logic
    try:
        instance.clean()
    except DjangoValidationError as e:
        if config.strict_validation:
            raise NovaValidationError.from_django(e) from e
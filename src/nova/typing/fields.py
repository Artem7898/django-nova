"""
Custom typed fields for Django.
Adds Pydantic-compatible type annotations to Django descriptors.
"""
from __future__ import annotations

from typing import Any

from django.db import models


class TypedField[T](models.Field):
    """
    A Django Field that explicitly declares its Python type.
    
    Example:
        class MyModel(NovaModel):
            score: float = TypedField[float](FloatField)
    """
    def __init__(self, field_instance: models.Field, **kwargs: Any) -> None:
        self._inner_field = field_instance
        super().__init__(**kwargs)
        # Copy constraints from inner field
        self.null = field_instance.null
        self.blank = field_instance.blank
        self.default = field_instance.default
        self.max_length = getattr(field_instance, 'max_length', None)

    def db_type(self, connection: Any) -> str:
        return self._inner_field.db_type(connection)

    def get_prep_value(self, value: Any) -> Any:
        return self._inner_field.get_prep_value(value)

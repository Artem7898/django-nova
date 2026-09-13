"""Typed wrappers for scalar Django fields.

The type parameter documents the Python value type; it does not perform runtime
validation. Conversion and validation are delegated to the inner Django field.
Relations, file descriptors, and automatic timestamp hooks require additional
integration and are not covered by this scalar wrapper contract.
"""

from __future__ import annotations

from typing import Any

from django.db import models


class TypedField[T](models.Field):
    """Preserve scalar field behavior and reconstructible migration metadata."""

    def __init__(
        self,
        inner_field: models.Field[Any, Any],
        **kwargs: Any,
    ) -> None:
        # Explicit overrides must affect conversion/validation and DB metadata
        # consistently, without modifying the caller's original field.
        if kwargs:
            _, _, args, inner_kwargs = inner_field.deconstruct()
            inner_kwargs.update(kwargs)
            inner_field = type(inner_field)(*args, **inner_kwargs)

        self._inner_field = inner_field
        # Call the base implementation to copy only common Django options;
        # specialized options (e.g. decimal_places) stay on the inner field.
        _, _, _, common_kwargs = models.Field.deconstruct(inner_field)
        super().__init__(**common_kwargs)

    def deconstruct(self) -> tuple[str | None, str, list[Any], dict[str, Any]]:
        name, path, _, kwargs = super().deconstruct()
        # Include explicit values even where they equal Django defaults:
        # null=False must override an inner field originally using null=True.
        for option in (
            "primary_key",
            "max_length",
            "unique",
            "blank",
            "null",
            "db_index",
            "default",
            "db_default",
            "editable",
            "serialize",
            "choices",
            "help_text",
            "db_column",
            "db_comment",
            "unique_for_date",
            "unique_for_month",
            "unique_for_year",
        ):
            kwargs[option] = getattr(self, option)
        # Use an independent inner field so clone() does not share mutable state.
        return name, path, [self._inner_field.clone()], kwargs

    def to_python(self, value: Any) -> Any:
        return self._inner_field.to_python(value)

    def clean(self, value: Any, model_instance: models.Model | None) -> Any:
        return self._inner_field.clean(value, model_instance)

    def db_type(self, connection: Any) -> str | None:
        return self._inner_field.db_type(connection)

    def get_prep_value(self, value: Any) -> Any:
        return self._inner_field.get_prep_value(value)

    def get_db_prep_value(
        self,
        value: Any,
        connection: Any,
        prepared: bool = False,
    ) -> Any:
        return self._inner_field.get_db_prep_value(value, connection, prepared=prepared)

    def get_db_prep_save(self, value: Any, connection: Any) -> Any:
        return self._inner_field.get_db_prep_save(value, connection)

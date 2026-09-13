"""Tests for Nova typed Django fields."""

from __future__ import annotations

from unittest.mock import MagicMock

from django.db import models

from nova.typing.fields import TypedField


class TestTypedField:
    def test_initializes_with_inner_field(self) -> None:
        inner_field = models.CharField(
            max_length=255,
            null=True,
            blank=True,
            default="default",
        )

        field = TypedField(inner_field)

        assert field._inner_field is inner_field

    def test_copies_field_constraints(self) -> None:
        inner_field = models.CharField(
            max_length=255,
            null=True,
            blank=True,
            default="default",
        )

        field = TypedField(inner_field)

        assert field.null is True
        assert field.blank is True
        assert field.default == "default"
        assert field.max_length == 255

    def test_copies_none_max_length(self) -> None:
        inner_field = models.TextField(
            null=False,
            blank=False,
            default=models.NOT_PROVIDED,
        )

        field = TypedField(inner_field)

        assert field.max_length is None

    def test_db_type_delegates_to_inner_field(self) -> None:
        inner_field = models.CharField(max_length=255)

        db_type = MagicMock(return_value="varchar(255)")
        inner_field.db_type = db_type

        field = TypedField(inner_field)
        connection = MagicMock()

        result = field.db_type(connection)

        assert result == "varchar(255)"
        db_type.assert_called_once_with(connection)

    def test_get_prep_value_delegates_to_inner_field(self) -> None:
        inner_field = models.CharField(max_length=255)

        get_prep_value = MagicMock(return_value="prepared-value")
        inner_field.get_prep_value = get_prep_value

        field = TypedField(inner_field)

        result = field.get_prep_value("test-value")

        assert result == "prepared-value"
        get_prep_value.assert_called_once_with("test-value")

    def test_get_prep_value_preserves_none(self) -> None:
        inner_field = models.CharField(max_length=255)

        get_prep_value = MagicMock(return_value=None)
        inner_field.get_prep_value = get_prep_value

        field = TypedField(inner_field)

        result = field.get_prep_value(None)

        assert result is None
        get_prep_value.assert_called_once_with(None)

    def test_delegates_different_values_without_modification(self) -> None:
        inner_field = models.CharField(max_length=255)
        get_prep_value = MagicMock(side_effect=lambda value: value)
        inner_field.get_prep_value = get_prep_value

        values = [0, 1, 1.5, "text", None]

        field = TypedField(inner_field)

        for value in values:
            result = field.get_prep_value(value)

            assert result == value

        assert get_prep_value.call_count == len(values)

"""Scalar conversion and migration contracts for TypedField."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import connection, models
from django.db.migrations.writer import MigrationWriter
from django.utils.module_loading import import_string

from nova.typing.fields import TypedField


@pytest.mark.parametrize(
    ("inner", "raw", "expected"),
    [
        (models.IntegerField(), "42", 42),
        (models.DecimalField(max_digits=6, decimal_places=2), "12.50", Decimal("12.50")),
        (models.DateField(), "2026-09-13", date(2026, 9, 13)),
        (
            models.UUIDField(),
            "12345678-1234-5678-1234-567812345678",
            UUID("12345678-1234-5678-1234-567812345678"),
        ),
    ],
    ids=["integer", "decimal", "date", "uuid"],
)
def test_to_python_and_clean_preserve_scalar_type(inner, raw, expected):
    field = TypedField(inner)
    for converted in (field.to_python(raw), field.clean(raw, None)):
        assert type(converted) is type(expected)
        assert converted == expected


@pytest.mark.parametrize(
    ("inner", "raw"),
    [
        (models.IntegerField(), "invalid"),
        (models.CharField(max_length=3), "long"),
        (models.IntegerField(validators=[MinValueValidator(10)]), 9),
        (models.DecimalField(max_digits=5, decimal_places=2), Decimal("0.001")),
        (models.CharField(max_length=10, choices=[("a", "A")]), "b"),
    ],
    ids=["conversion", "length", "custom_validator", "decimal_places", "choices"],
)
def test_clean_rejects_invalid_value(inner, raw):
    with pytest.raises(ValidationError):
        TypedField(inner).clean(raw, None)


def test_explicit_overrides_do_not_mutate_original():
    inner = models.CharField(max_length=3, null=True, blank=True)
    field = TypedField(inner, max_length=5, null=False, blank=False)
    assert field.clean("hello", None) == "hello"
    with pytest.raises(ValidationError):
        field.clean(None, None)
    with pytest.raises(ValidationError):
        field.clean("", None)
    assert inner.max_length == 3
    assert inner.null is True
    assert inner.blank is True


def test_deconstruct_reconstruct_preserves_options():
    field = TypedField(models.DecimalField(max_digits=7, decimal_places=2), null=True, blank=True)
    field.set_attributes_from_name("amount")
    name, path, args, kwargs = field.deconstruct()
    assert name == "amount"
    rebuilt = import_string(path)(*args, **kwargs)
    assert rebuilt.null is True
    assert rebuilt.clean(None, None) is None
    assert rebuilt.clean("12.50", None) == Decimal("12.50")
    with pytest.raises(ValidationError):
        rebuilt.clean("0.001", None)


def test_clone_has_independent_inner_field():
    field = TypedField(models.CharField(max_length=5))
    cloned = field.clone()
    assert isinstance(cloned, TypedField)
    assert cloned._inner_field is not field._inner_field
    assert cloned.clean("hello", None) == "hello"
    with pytest.raises(ValidationError):
        cloned.clean("longer", None)


def test_migration_writer_round_trip():
    field = TypedField(
        models.IntegerField(validators=[MinValueValidator(0)]),
        default=0,
        null=True,
        blank=True,
        db_column="number_value",
    )
    expression, imports = MigrationWriter.serialize(field)
    namespace = {}
    exec("\n".join(sorted(imports)), namespace)
    rebuilt = eval(expression, namespace)
    assert isinstance(rebuilt, TypedField)
    assert rebuilt.default == 0
    assert rebuilt.db_column == "number_value"
    assert rebuilt.clean(None, None) is None
    assert rebuilt.clean("42", None) == 42
    with pytest.raises(ValidationError):
        rebuilt.clean(-1, None)
    assert MigrationWriter.serialize(rebuilt) == (expression, imports)


def test_callable_default_survives_clone_without_evaluation():
    field = TypedField(models.UUIDField(default=uuid4))
    cloned = field.clone()
    assert cloned.default is uuid4
    assert isinstance(cloned.get_default(), UUID)


@pytest.mark.parametrize("prepared", [False, True])
def test_database_preparation_matches_inner(prepared):
    inner = models.DecimalField(max_digits=7, decimal_places=2)
    field = TypedField(inner)
    value = Decimal("12.50")
    assert field.get_db_prep_value(value, connection, prepared=prepared) == inner.get_db_prep_value(
        value,
        connection,
        prepared=prepared,
    )
    assert field.get_db_prep_save(value, connection) == inner.get_db_prep_save(value, connection)

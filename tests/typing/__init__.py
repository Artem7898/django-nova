"""Contract tests for Nova's Django typing boundary."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models

from nova.typing.django import (
    get_concrete_fields,
    get_model_field,
    get_model_pk,
    is_concrete_field,
    is_generic_foreign_key,
    safe_clean_field,
    safe_get_attname,
)


class RelatedAuthor(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "typing_boundary"


class TypingBoundaryModel(models.Model):
    name = models.CharField(max_length=100)
    age = models.IntegerField()
    author = models.ForeignKey(
        RelatedAuthor,
        on_delete=models.CASCADE,
        related_name="typing_boundary_models",
    )
    tags = models.ManyToManyField(
        RelatedAuthor,
        related_name="typing_boundary_tags",
    )
    content_type = models.ForeignKey(
        "contenttypes.ContentType",
        on_delete=models.CASCADE,
        null=True,
    )
    object_id = models.PositiveIntegerField(null=True)
    generic_object = GenericForeignKey(
        "content_type",
        "object_id",
    )

    class Meta:
        app_label = "typing_boundary"


class CustomCleanField(models.CharField):
    """Field used to verify safe_clean_field()."""

    def clean(
        self,
        value: Any,
        model_instance: models.Model | None,
    ) -> Any:
        value = super().clean(value, model_instance)

        if value == "INVALID":
            from django.core.exceptions import ValidationError

            raise ValidationError("Invalid value")

        return value


class CleanModel(models.Model):
    value = CustomCleanField(max_length=100)

    class Meta:
        app_label = "typing_boundary"


def test_get_model_pk_returns_concrete_primary_key() -> None:
    """Primary-key lookup returns the actual Django PK field."""
    pk = get_model_pk(TypingBoundaryModel)

    assert pk is not None
    assert pk.name == "id"
    assert pk.attname == "id"
    assert pk.auto_created is True


def test_get_model_pk_non_strict_returns_none_for_missing_pk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-strict PK lookup can represent an absent metadata PK."""
    meta = TypingBoundaryModel._meta

    monkeypatch.setattr(meta, "pk", None)

    assert (
        get_model_pk(
            TypingBoundaryModel,
            strict=False,
        )
        is None
    )


def test_get_model_pk_strict_raises_for_missing_pk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict PK lookup fails explicitly when metadata has no PK."""
    meta = TypingBoundaryModel._meta

    monkeypatch.setattr(meta, "pk", None)

    with pytest.raises(
        AttributeError,
        match="TypingBoundaryModel has no primary key",
    ):
        get_model_pk(TypingBoundaryModel)


def test_get_model_field_returns_concrete_field() -> None:
    """Concrete ORM fields are exposed through the boundary."""
    field = get_model_field(
        TypingBoundaryModel,
        "name",
    )

    assert field is not None
    assert field.name == "name"
    assert field.attname == "name"


def test_get_model_field_returns_relation_field() -> None:
    """
    get_model_field() exposes relation metadata.

    Query planning needs the complete ORM contract, not only concrete
    database fields.
    """
    field = get_model_field(
        TypingBoundaryModel,
        "author",
    )

    assert field is not None
    assert field.name == "author"


def test_get_model_field_returns_none_for_missing_field() -> None:
    """Unknown ORM fields are represented by None."""
    assert (
        get_model_field(
            TypingBoundaryModel,
            "does_not_exist",
        )
        is None
    )


def test_get_concrete_fields_excludes_many_to_many() -> None:
    """Many-to-many fields are not concrete database columns."""
    fields = dict(
        get_concrete_fields(TypingBoundaryModel),
    )

    assert "name" in fields
    assert "age" in fields
    assert "author" in fields
    assert "id" in fields
    assert "tags" not in fields


def test_get_concrete_fields_excludes_generic_foreign_key() -> None:
    """GenericForeignKey never leaks into concrete field contracts."""
    fields = dict(
        get_concrete_fields(TypingBoundaryModel),
    )

    assert "generic_object" not in fields


def test_get_concrete_fields_contains_only_concrete_fields() -> None:
    """Every returned field satisfies Nova's concrete-field contract."""
    fields = get_concrete_fields(TypingBoundaryModel)

    assert fields

    for name, model_field in fields:
        assert name == model_field.name
        assert is_concrete_field(model_field)
        assert isinstance(model_field.attname, str)


def test_is_concrete_field_accepts_normal_django_field() -> None:
    """Normal Django database fields satisfy the boundary."""
    field = TypingBoundaryModel._meta.get_field("name")

    assert is_concrete_field(field)


def test_is_concrete_field_rejects_many_to_many_field() -> None:
    """Many-to-many fields are intentionally outside the concrete contract."""
    field = TypingBoundaryModel._meta.get_field("tags")

    assert not is_concrete_field(field)


def test_is_concrete_field_rejects_generic_foreign_key() -> None:
    """GenericForeignKey is virtual metadata, not a concrete field."""
    field = TypingBoundaryModel.generic_object

    assert isinstance(field, GenericForeignKey)
    assert not is_concrete_field(field)


def test_is_generic_foreign_key_detects_gfk() -> None:
    """GenericForeignKey detection is explicit and stable."""
    field = TypingBoundaryModel.generic_object

    assert is_generic_foreign_key(field)


def test_is_generic_foreign_key_rejects_normal_field() -> None:
    """Normal fields are not classified as GenericForeignKey."""
    field = TypingBoundaryModel._meta.get_field("name")

    assert not is_generic_foreign_key(field)


def test_safe_get_attname_returns_attname_for_concrete_field() -> None:
    """Concrete fields expose their Django attribute name."""
    field = TypingBoundaryModel._meta.get_field("author")

    assert safe_get_attname(field) == "author_id"


def test_safe_get_attname_returns_none_for_virtual_field() -> None:
    """Virtual fields have no concrete database attribute."""
    field = TypingBoundaryModel.generic_object

    assert safe_get_attname(field) is None


def test_safe_get_attname_returns_none_for_arbitrary_object() -> None:
    """Non-Django objects cannot leak into the field boundary."""
    assert safe_get_attname(object()) is None


def test_safe_clean_field_validates_concrete_field() -> None:
    """Concrete field validation is delegated to Django."""
    field = CleanModel._meta.get_field("value")
    instance = CleanModel(value="valid")

    assert (
        safe_clean_field(
            field,
            "valid",
            instance,
        )
        == "valid"
    )


def test_safe_clean_field_preserves_django_validation_error() -> None:
    """Django ValidationError crosses this low-level boundary unchanged."""
    from django.core.exceptions import ValidationError

    field = CleanModel._meta.get_field("value")
    instance = CleanModel(value="INVALID")

    with pytest.raises(ValidationError, match="Invalid value"):
        safe_clean_field(
            field,
            "INVALID",
            instance,
        )


def test_safe_clean_field_ignores_non_concrete_field() -> None:
    """Virtual metadata does not execute field-level validation."""
    field = TypingBoundaryModel.generic_object
    instance = TypingBoundaryModel()

    value = object()

    assert (
        safe_clean_field(
            field,
            value,
            instance,
        )
        is value
    )

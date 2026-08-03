
"""
Validation correctness suite for the Nova ORM validation contract.

The suite verifies that:

1. Pydantic is the canonical business/data validation layer.
2. NovaModel.save() validates before persistence.
3. Full-model validation is preserved for update_fields.
4. Cross-field invariants are enforced.
5. Django uniqueness and constraint validation remain part of the ORM boundary.
6. Infrastructure errors are never silently converted into validation errors.
7. QuerySet bulk write operations explicitly bypass model.save().
8. strict_validation=False is an explicit compatibility mode for Pydantic
   validation and does not promise to disable database-level constraints.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from pydantic import BaseModel, model_validator

from nova import NovaConfig, NovaModel
from nova.core.exceptions import NovaValidationError

# ---------------------------------------------------------------------------
# Test schemas
# ---------------------------------------------------------------------------


class GrantSchema(BaseModel):
    title: str
    budget: Decimal
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> GrantSchema:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class BlankTestProfileSchema(BaseModel):
    """
    blank=True in Django must not implicitly become Optional in Pydantic.
    """

    bio: str


class UniqueSchema(BaseModel):
    code: str


class ConstraintSchema(BaseModel):
    age: int


class StrictFalseSchema(BaseModel):
    value: int


class CleanFailSchema(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class Grant(NovaModel):
    title = models.CharField(max_length=200)
    budget = models.DecimalField(max_digits=10, decimal_places=2)
    start_date = models.DateField()
    end_date = models.DateField()

    _nova_config = NovaConfig(
        pydantic_schema=GrantSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class BlankTestProfile(NovaModel):
    bio = models.CharField(
        max_length=500,
        blank=True,
        null=False,
    )

    _nova_config = NovaConfig(
        pydantic_schema=BlankTestProfileSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class UniqueModel(NovaModel):
    code = models.CharField(
        max_length=50,
        unique=True,
    )

    _nova_config = NovaConfig(
        pydantic_schema=UniqueSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class ConstraintModel(NovaModel):
    age = models.IntegerField()

    _nova_config = NovaConfig(
        pydantic_schema=ConstraintSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(age__gte=18),
                name="age_gte_18",
            ),
        ]


class StrictFalseModel(NovaModel):
    value = models.IntegerField()

    _nova_config = NovaConfig(
        pydantic_schema=StrictFalseSchema,
        strict_validation=False,
    )

    class Meta:
        app_label = "tests"


class CleanFailModel(NovaModel):
    name = models.CharField(max_length=100)

    _nova_config = NovaConfig(
        pydantic_schema=CleanFailSchema,
        strict_validation=True,
    )

    def clean(self) -> None:
        raise DjangoValidationError("Custom business rule failed")

    class Meta:
        app_label = "tests"


# ---------------------------------------------------------------------------
# Save validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveValidation:
    """Validate the primary NovaModel.save() contract."""

    def test_valid_save(self) -> None:
        grant = Grant(
            title="Valid Grant",
            budget=Decimal("1000.00"),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        grant.save()

        assert grant.id is not None

    def test_invalid_save_is_rejected_before_persistence(self) -> None:
        grant = Grant(
            title="Valid Grant",
            budget=Decimal("1000.00"),
            start_date=date(2024, 12, 31),
            end_date=date(2024, 1, 1),
        )

        with pytest.raises(NovaValidationError):
            grant.save()

        assert not Grant.objects.filter(title="Valid Grant").exists()

    def test_cross_field_validation_is_enforced(self) -> None:
        grant = Grant(
            title="Cross Field Fail",
            budget=Decimal("1000.00"),
            start_date=date(2024, 6, 1),
            end_date=date(2024, 1, 1),
        )

        with pytest.raises(
            NovaValidationError,
            match="end_date must be after start_date",
        ):
            grant.save()

    def test_save_update_fields_runs_full_validation(self) -> None:
        """
        update_fields must not weaken the validation contract.

        Cross-field invariants require the complete model state, therefore
        Nova validates the complete instance even when only one field is
        persisted.
        """
        grant = Grant.objects.create(
            title="Initial",
            budget=Decimal("1000.00"),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        grant.start_date = date(2025, 1, 1)

        with pytest.raises(
            NovaValidationError,
            match="end_date must be after start_date",
        ):
            grant.save(update_fields=["start_date"])

        grant.refresh_from_db()

        assert grant.start_date == date(2024, 1, 1)
        assert grant.end_date == date(2024, 12, 31)

    def test_validation_error_is_stable_nova_exception(self) -> None:
        grant = Grant(
            title="Invalid",
            budget=Decimal("1000.00"),
            start_date=date(2024, 12, 31),
            end_date=date(2024, 1, 1),
        )

        with pytest.raises(NovaValidationError) as exc_info:
            grant.save()

        assert isinstance(exc_info.value, NovaValidationError)
        assert not isinstance(exc_info.value, DjangoValidationError)

    def test_blank_true_does_not_make_field_optional(self) -> None:
        """
        Django blank=True is a form-level concept.

        It must not change the Pydantic contract from `str` to
        `str | None`.
        """
        profile = BlankTestProfile(bio="")

        profile.save()

        assert profile.pk is not None

    def test_none_is_rejected_when_schema_requires_string(self) -> None:
        profile = BlankTestProfile(bio=None)

        with pytest.raises(NovaValidationError):
            profile.save()

        assert not BlankTestProfile.objects.filter(pk=profile.pk).exists()


# ---------------------------------------------------------------------------
# Django validation boundary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDjangoValidationBoundary:
    """
    Verify that Django's own model-level validation remains part of the
    Nova ORM boundary after Pydantic validation succeeds.
    """

    def test_unique_field_violation_is_rejected(self) -> None:
        UniqueModel.objects.create(code="NOVA-001")

        duplicate = UniqueModel(code="NOVA-001")

        with pytest.raises(NovaValidationError) as exc_info:
            duplicate.save()

        assert "unique" in str(exc_info.value).lower()

    def test_check_constraint_violation_is_rejected(self) -> None:
        invalid = ConstraintModel(age=16)

        with pytest.raises(NovaValidationError) as exc_info:
            invalid.save()

        message = str(exc_info.value).lower()

        assert "constraint" in message or "check" in message

    def test_custom_clean_error_is_wrapped(self) -> None:
        obj = CleanFailModel(name="Test")

        with pytest.raises(
            NovaValidationError,
            match="Custom business rule failed",
        ):
            obj.save()


# ---------------------------------------------------------------------------
# Validation ordering
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidationOrdering:
    """Verify that validation stages execute in the correct order."""

    def test_pydantic_failure_short_circuits_django_validation(self) -> None:
        """
        Pydantic validation must fail before Django uniqueness/constraint
        checks are reached.
        """
        obj = Grant(
            title="Invalid",
            budget=Decimal("1000.00"),
            start_date=date(2024, 12, 31),
            end_date=date(2024, 1, 1),
        )

        with patch(
            "nova.validation.unified.validate_model_instance"
        ) as validate:
            validate.side_effect = NovaValidationError(
                "Forced Pydantic failure"
            )

            with pytest.raises(
                NovaValidationError,
                match="Forced Pydantic failure",
            ):
                obj.save()

            validate.assert_called_once()

    def test_infrastructure_error_is_not_masked(self) -> None:
        """
        Runtime/infrastructure failures must remain distinguishable from
        business validation failures.
        """
        obj = UniqueModel(code="SAFE-CODE")

        with patch.object(
            obj,
            "to_pydantic",
            side_effect=RuntimeError("simulated infrastructure failure"),
        ), pytest.raises(
            RuntimeError,
            match="simulated infrastructure failure",
        ):
            obj.save()


# ---------------------------------------------------------------------------
# Compatibility mode
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStrictValidationCompatibility:
    """
    strict_validation=False disables Nova's Pydantic validation layer.

    It does NOT claim that database-level constraints disappear. Database
    integrity remains authoritative.
    """

    def test_strict_validation_false_bypasses_pydantic(self) -> None:
        obj = StrictFalseModel(value=-100)

        obj.save()

        assert StrictFalseModel.objects.filter(
            pk=obj.pk,
            value=-100,
        ).exists()

    def test_strict_validation_false_is_explicit_compatibility_mode(self) -> None:
        obj = StrictFalseModel(value=-100)

        with patch.object(
            obj,
            "to_pydantic",
            side_effect=AssertionError(
                "Pydantic validation must not run in compatibility mode"
            ),
        ):
            obj.save()

        assert StrictFalseModel.objects.filter(
            pk=obj.pk,
            value=-100,
        ).exists()


# ---------------------------------------------------------------------------
# ORM write-path contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestORMWritePaths:
    """
    Explicitly document Django ORM operations that do not call
    NovaModel.save().

    These operations are intentionally outside the model validation hook.
    """

    def test_queryset_update_bypasses_model_save(self) -> None:
        obj = StrictFalseModel.objects.create(value=10)

        StrictFalseModel.objects.filter(pk=obj.pk).update(value=-100)

        obj.refresh_from_db()

        assert obj.value == -100

    def test_bulk_create_bypasses_model_save(self) -> None:
        objects = [
            StrictFalseModel(value=10),
            StrictFalseModel(value=20),
        ]

        StrictFalseModel.objects.bulk_create(objects)

        assert StrictFalseModel.objects.filter(
            value__in=[10, 20]
        ).count() == 2

    def test_bulk_update_bypasses_model_save(self) -> None:
        objects = [
            StrictFalseModel.objects.create(value=10),
            StrictFalseModel.objects.create(value=20),
        ]

        for obj in objects:
            obj.value = -obj.value

        StrictFalseModel.objects.bulk_update(
            objects,
            ["value"],
        )

        values = set(
            StrictFalseModel.objects.filter(
                pk__in=[obj.pk for obj in objects]
            ).values_list("value", flat=True)
        )

        assert values == {-10, -20}


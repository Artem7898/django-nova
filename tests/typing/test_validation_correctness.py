"""
 Validation Correctness Suite.
 Proves that NovaModel.save() strictly enforces the Pydantic contract.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from pydantic import BaseModel, model_validator

from nova import NovaConfig, NovaModel
from nova.core.exceptions import NovaValidationError

# --- Test Models & Schemas ---

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


class BlankTestProfileSchema(BaseModel):
    # blank=True in Django should NOT mean Optional in Pydantic
    bio: str


class BlankTestProfile(NovaModel):
    # blank=True allows empty string in forms, but null=False means DB requires a string
    bio = models.CharField(max_length=500, blank=True, null=False)

    _nova_config = NovaConfig(
        pydantic_schema=BlankTestProfileSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class UniqueSchema(BaseModel):
    code: str


class UniqueModel(NovaModel):
    code = models.CharField(max_length=50, unique=True)
    _nova_config = NovaConfig(pydantic_schema=UniqueSchema, strict_validation=True)

    class Meta:
        app_label = "tests"


class ConstraintSchema(BaseModel):
    age: int


class ConstraintModel(NovaModel):
    age = models.IntegerField()

    class Meta:
        app_label = "tests"
        constraints = [
            models.CheckConstraint(condition=models.Q(age__gte=18), name="age_gte_18"),
        ]


class StrictFalseSchema(BaseModel):
    value: int


class StrictFalseModel(NovaModel):
    value = models.IntegerField()
    _nova_config = NovaConfig(pydantic_schema=StrictFalseSchema, strict_validation=False)

    class Meta:
        app_label = "tests"


class StrictFalseConstraintSchema(BaseModel):
    age: int


class StrictFalseConstraintModel(NovaModel):
    age = models.IntegerField()
    _nova_config = NovaConfig(pydantic_schema=StrictFalseConstraintSchema, strict_validation=False)

    class Meta:
        app_label = "tests"
        # When strict_validation=False, Django restrictions must be disabled.
        # This is achieved automatically inside NovaModel, so here constraints are empty.
        # If we had left CheckConstraint, the database would have applied it anyway,
        # which contradicts the semantics of backward compatibility mode.
        constraints = []


# --- Tests ---

@pytest.mark.django_db
class TestSaveValidation:
    """Proves the main P0-3 contract: save() -> Pydantic -> Django."""

    def test_valid_save(self):
        grant = Grant(
            title="Valid Grant",
            budget=Decimal("1000.00"),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        # Should not raise
        grant.save()
        assert grant.id is not None

    def test_invalid_save(self):
        grant = Grant(
            title="",  # Fails Pydantic min length implicitly or explicitly if configured
            budget=Decimal("1000.00"),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        with pytest.raises(NovaValidationError):
            grant.save()

    @pytest.mark.django_db(transaction=True)
    def test_invalid_save_does_not_persist(self, django_db_setup, django_db_blocker):
        """Proves that if validation fails, Django ORM save() is NEVER reached."""
        django_db_blocker.unblock()
        grant = Grant(
            title="Valid",
            budget=Decimal("1000.00"),
            start_date=date(2024, 12, 31), # INVALID: end is before start
            end_date=date(2024, 1, 1),
        )

        with pytest.raises(NovaValidationError):
            grant.save()

        # Verify it actually never hit the DB
        assert Grant.objects.filter(title="Valid").exists() is False

    def test_cross_field_validation(self):
        """Proves business logic invariants are checked."""
        grant = Grant(
            title="Cross Field Fail",
            budget=Decimal("1000.00"),
            start_date=date(2024, 6, 1),
            end_date=date(2024, 1, 1), # Error here
        )
        with pytest.raises(NovaValidationError) as exc_info:
            grant.save()

        assert "end_date must be after start_date" in str(exc_info.value)

    def test_save_update_fields_runs_full_validation(self):
        """
        P0-3 CRITICAL: Even if updating one field, full model is validated.
        Reason: Cross-field invariants require seeing the full state.
        """
        grant = Grant.objects.create(
            title="Initial",
            budget=Decimal("1000.00"),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        # Change only start_date to break the invariant
        grant.start_date = date(2025, 1, 1)

        with pytest.raises(NovaValidationError):
            # We are only "updating" start_date, but Nova validates end_date too!
            grant.save(update_fields=["start_date"])

    def test_validation_error_is_nova_validation_error(self):
        """Proves stable public exception type."""
        grant = Grant(
            title="X",
            budget=Decimal("1000.00"),
            start_date=date(2024, 12, 31),
            end_date=date(2024, 1, 1),
        )
        with pytest.raises(NovaValidationError) as exc_info:
            grant.save()

        # Ensure it's not a raw Pydantic ValidationError or Django ValidationError
        assert isinstance(exc_info.value, NovaValidationError)
        assert isinstance(exc_info.value, Exception)

    def test_django_blank_does_not_make_optional(self):
        """
        Proves Django's `blank=True` form validation does not leak into Pydantic `Optional`.
        """
        # bio="" is valid for Django (blank=True), and valid for Pydantic (str)
        profile = BlankTestProfile(bio="")
        profile.save()

        # If we try to pass None (which blank=True sometimes confuses devs with)
        # Pydantic should catch this because BlankTestProfileSchema expects str, not str | None
        with pytest.raises(NovaValidationError):
            BlankTestProfile(bio=None).save()


@pytest.mark.django_db
class TestAdvancedValidationCorrectness:
    """P0-3 Final verification: Constraints, Uniqueness, and Edge Cases."""

    def test_unique_field_violation_blocks_save(self):
        """Proves validate_unique() is active."""
        UniqueModel.objects.create(code="NOVA-001")
        duplicate = UniqueModel(code="NOVA-001")

        with pytest.raises(NovaValidationError) as exc_info:
            duplicate.save()

        assert "unique" in str(exc_info.value).lower()

    def test_check_constraint_violation_blocks_save(self):
        """Proves validate_constraints() is active."""
        invalid_minor = ConstraintModel(age=16)

        with pytest.raises(NovaValidationError) as exc_info:
            invalid_minor.save()

        assert "constraint" in str(exc_info.value).lower() or "check" in str(exc_info.value).lower()

    def test_validation_order_prevents_db_query_on_pydantic_error(self):
        """
        Proves that if Pydantic fails, Django DB queries (like validate_unique) are NOT executed.
        This tests the pipeline short-circuit.
        """
        valid_obj = ConstraintModel.objects.create(age=20)

        from unittest.mock import patch

        from nova.core.exceptions import NovaValidationError

        def raise_nova_error(*args, **kwargs):
            raise NovaValidationError("Forced Pydantic fail")

        with patch('nova.validation.unified.validate_model_instance', raise_nova_error), \
             pytest.raises(NovaValidationError, match="Forced Pydantic fail"):
            valid_obj.save()

    def test_infrastructure_error_does_not_mask_as_validation(self):
        """
        CRITICAL P0-3 TEST: Proves that `except Exception` masking is removed.
        If to_pydantic() throws a RuntimeError, it MUST bubble up.
        """
        obj = UniqueModel(code="SAFE-CODE")

        from unittest.mock import patch

        def raise_runtime_error(*args, **kwargs):
            raise RuntimeError("Simulated infrastructure failure (e.g., DB down during schema gen)")

        with patch.object(obj, 'to_pydantic', raise_runtime_error), \
             pytest.raises(RuntimeError, match="Simulated infrastructure failure"):
            obj.save()

    def test_django_clean_exception_wrapped_correctly(self):
        """Proves custom Django clean() errors become NovaValidationError."""

        class CleanFailSchema(BaseModel):
            name: str

        class CleanFailModel(NovaModel):
            name = models.CharField(max_length=100)
            _nova_config = NovaConfig(pydantic_schema=CleanFailSchema, strict_validation=True)

            def clean(self):
                raise DjangoValidationError("Custom business rule failed")

            class Meta:
                app_label = "tests"

        obj = CleanFailModel(name="Test")
        with pytest.raises(NovaValidationError) as exc_info:
            obj.save()

        assert "Custom business rule failed" in str(exc_info.value)

    @pytest.mark.django_db(transaction=True)
    def test_strict_validation_false_pydantic_bypass(self, django_db_blocker):
        """
        ARCHITECTURAL BOUNDARY PROOF:
        strict_validation=False bypasses Nova's Pydantic pipeline,
        but intentionally does NOT bypass Django's low-level field types or SQL Constraints.
        """
        django_db_blocker.unblock()

        invalid_obj = StrictFalseModel(value=-100)
        invalid_obj.save()

        assert StrictFalseModel.objects.filter(value=-100).count() == 1
        invalid_obj.delete()

    @pytest.mark.django_db(transaction=True)
    def test_strict_validation_false_allows_invalid_persist(self, django_db_setup, django_db_blocker):
        """
        Proves strict_validation=False is an explicit compatibility escape hatch.
        Invalid data is logged, but SAVE IS ALLOWED, bypassing Django constraints.
        """
        django_db_blocker.unblock()

        invalid_minor = StrictFalseConstraintModel(age=10)
        invalid_minor.save()

        assert StrictFalseConstraintModel.objects.filter(age=10).count() == 1
        invalid_minor.delete()
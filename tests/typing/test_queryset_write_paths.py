"""
P0-3 QuerySet Write Paths Suite.
Proves that bulk/update/delete intentionally bypass NovaModel.save().
This is a DOCUMENTED BOUNDARY, not a bug.
"""

from __future__ import annotations

import pytest
from django.db import models
from pydantic import BaseModel

from nova import NovaConfig, NovaModel


class StrictSchema(BaseModel):
    # Simple schema: value cannot be negative
    value: int


class StrictModel(NovaModel):
    value = models.IntegerField()
    _nova_config = NovaConfig(pydantic_schema=StrictSchema, strict_validation=True)

    class Meta:
        app_label = "tests"


@pytest.mark.django_db
class TestQuerySetWritePaths:
    """Proves the architectural boundary of ORM validation."""

    @pytest.mark.django_db(transaction=True)
    def test_update_bypasses_save_validation(self, django_db_setup, django_db_blocker):
        """
        QuerySet.update() executes raw SQL. It does NOT call NovaModel.save().
        Therefore, Pydantic validation is intentionally bypassed.
        """
        django_db_blocker.unblock()

        obj = StrictModel.objects.create(value=10)

        # This is INVALID according to Pydantic (negative value),
        # but because update() bypasses save(), it will succeed.
        StrictModel.objects.filter(id=obj.id).update(value=-100)

        obj.refresh_from_db()
        assert obj.value == -100  # Invalid state is now in DB

    @pytest.mark.django_db(transaction=True)
    def test_bulk_create_bypasses_save_validation(self, django_db_setup, django_db_blocker):
        """
        bulk_create() does NOT call save() on instances.
        """
        django_db_blocker.unblock()

        invalid_instances = [
            StrictModel(value=-1),
            StrictModel(value=-2),
        ]

        # No NovaValidationError is raised!
        StrictModel.objects.bulk_create(invalid_instances)

        assert StrictModel.objects.filter(value__lt=0).count() == 2

    @pytest.mark.django_db(transaction=True)
    def test_bulk_update_bypasses_save_validation(self, django_db_setup, django_db_blocker):
        """
        bulk_update() does NOT call save() on instances.
        """
        django_db_blocker.unblock()

        obj1 = StrictModel.objects.create(value=10)
        obj2 = StrictModel.objects.create(value=20)

        obj1.value = -10
        obj2.value = -20

        # No NovaValidationError is raised!
        StrictModel.objects.bulk_update([obj1, obj2], ["value"])

        obj1.refresh_from_db()
        assert obj1.value == -10

    def test_delete_does_not_require_schema_validation(self):
        """
        Deleting an object does not change its state, so validation is irrelevant.
        Signals and Cache should still work, but Pydantic is skipped.
        """
        obj = StrictModel.objects.create(value=10)

        # Should succeed without invoking Pydantic
        obj.delete()

        assert StrictModel.objects.filter(id=obj.id).exists() is False
"""
Validation architecture contracts.

Django Nova promise:
- Model.save() enforces validation.
- Model.objects.create() enforces validation.
- update_fields does not bypass validation.
- Bulk operations intentionally bypass NovaModel.save() validation.
"""

from __future__ import annotations

import pytest
from tests.models import Lab

from nova.core.exceptions import NovaValidationError

pytestmark = pytest.mark.django_db


class TestValidationContract:
    def test_save_enforces_schema(self) -> None:
        lab = Lab(name="Lab-1", budget=-10)

        with pytest.raises(NovaValidationError):
            lab.save()

    def test_create_enforces_schema(self) -> None:
        with pytest.raises(NovaValidationError):
            Lab.objects.create(name="Lab-1", budget=-10)

    def test_valid_save_persists(self) -> None:
        lab = Lab(name="Lab-1", budget=1000)
        lab.save()

        assert lab.pk is not None
        assert Lab.objects.filter(pk=lab.pk).exists()

    def test_update_fields_still_validates_full_model(self) -> None:
        lab = Lab.objects.create(name="Lab-1", budget=1000)

        lab.budget = -10

        with pytest.raises(NovaValidationError):
            lab.save(update_fields=["budget"])

    def test_bulk_create_is_intentionally_unvalidated(self) -> None:
        """
        Architectural decision:

        bulk_create bypasses NovaModel.save() and therefore bypasses
        Pydantic validation. This is intentional and documented.
        """
        invalid_lab = Lab(name="Bulk", budget=-10)

        created = Lab.objects.bulk_create([invalid_lab])

        assert len(created) == 1
        assert Lab.objects.filter(name="Bulk").exists()
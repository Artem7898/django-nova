"""Query contract tests for Django Admin schema compiler."""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin

from nova.ecosystem import compile_admin
from tests.models import Lab
from tests.query.contracts import SchemaCompilerContract, ValidationReport
from tests.query.fixtures import lab_schema_expectation


@pytest.fixture()
def lab_admin_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(lab_schema_expectation("Admin: Lab"))


def _make_form():
    admin_cls = compile_admin(Lab)
    admin_instance = admin_cls(Lab, django_admin.site)
    return admin_instance.get_form(request=None)


@pytest.mark.django_db
def test_admin_form_validation_accepts_valid(lab_admin_contract) -> None:
    form_cls = _make_form()

    def validate_payload(payload):
        form = form_cls(data=payload)
        is_valid = form.is_valid()
        return ValidationReport(
            is_valid=is_valid,
            errors=dict(form.errors) if not is_valid else {},
            raw=form,
        )

    lab_admin_contract.check_accepts_valid_payload(validate_payload)


@pytest.mark.django_db
def test_admin_form_validation_rejects_invalid(lab_admin_contract) -> None:
    form_cls = _make_form()

    def validate_payload(payload):
        form = form_cls(data=payload)
        is_valid = form.is_valid()
        return ValidationReport(
            is_valid=is_valid,
            errors=dict(form.errors) if not is_valid else {},
            raw=form,
        )

    lab_admin_contract.check_rejects_invalid_payloads(validate_payload)


@pytest.mark.django_db
def test_admin_model_without_schema_raises() -> None:
    from tests.models import Article

    with pytest.raises(ValueError, match="pydantic_schema"):
        compile_admin(Article)

"""Query contract tests for DRF schema compiler."""

from __future__ import annotations

import pytest

pytest.importorskip("rest_framework")

from nova.ecosystem import to_drf_serializer
from tests.models import ArticleWithRelations, GrantWithSecret, Lab
from tests.query.contracts import SchemaCompilerContract, ValidationReport
from tests.query.fixtures import (
    article_relations_expectation,
    grant_secret_expectation,
    lab_schema_expectation,
)


@pytest.fixture()
def lab_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(lab_schema_expectation("DRF: Lab"))


def test_lab_projection_is_schema_only(lab_contract: SchemaCompilerContract) -> None:
    serializer_cls = to_drf_serializer(Lab)
    lab_contract.check_projection(serializer_cls().fields.keys())


def test_lab_projection_deterministic(lab_contract: SchemaCompilerContract) -> None:
    def factory():
        return to_drf_serializer(Lab)().fields.keys()

    lab_contract.check_deterministic_projection(factory)


def test_lab_validation_uses_pydantic(lab_contract: SchemaCompilerContract) -> None:
    serializer_cls = to_drf_serializer(Lab)

    def validate_payload(payload):
        serializer = serializer_cls(data=payload)
        is_valid = serializer.is_valid()
        return ValidationReport(
            is_valid=is_valid,
            errors=dict(serializer.errors) if not is_valid else {},
            raw=serializer,
        )

    lab_contract.check_accepts_valid_payload(validate_payload)
    lab_contract.check_rejects_invalid_payloads(validate_payload)


@pytest.fixture()
def secret_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(grant_secret_expectation("DRF: GrantWithSecret"))


def test_secret_field_hidden_from_drf(secret_contract: SchemaCompilerContract) -> None:
    serializer_cls = to_drf_serializer(GrantWithSecret)
    fields = serializer_cls().fields.keys()
    secret_contract.check_projection(fields)
    assert "secret_note" not in fields


@pytest.fixture()
def relations_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(article_relations_expectation("DRF: ArticleWithRelations"))


def test_fk_exposed_as_schema_name(relations_contract: SchemaCompilerContract) -> None:
    """New DRF compiler exposes schema names (author), not attnames."""
    serializer_cls = to_drf_serializer(ArticleWithRelations)
    fields = set(serializer_cls().fields.keys())
    relations_contract.check_projection(fields, use_drf_mapping=True)
    assert "author" in fields
    assert "author_id" not in fields


def test_model_without_schema_raises_value_error() -> None:
    from tests.models import Article

    with pytest.raises(ValueError, match="pydantic_schema"):
        to_drf_serializer(Article)


def test_drf_available_flag() -> None:
    from nova.ecosystem.drf import DRF_AVAILABLE

    assert DRF_AVAILABLE is True

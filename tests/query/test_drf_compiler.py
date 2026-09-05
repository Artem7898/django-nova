"""Contract tests for the Django Nova → DRF schema compiler."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

import pytest

from nova.ecosystem import to_drf_serializer
from tests.models import ArticleWithRelations, GrantWithSecret, Lab
from tests.query.contracts import (
    SchemaCompilerContract,
    ValidationReport,
)
from tests.query.fixtures import (
    article_relations_expectation,
    grant_secret_expectation,
    lab_schema_expectation,
)

drf_serializers = pytest.importorskip(
    "rest_framework.serializers",
    reason="Django REST Framework is required for DRF compiler tests",
)

# ============================================================================
# Shared helpers
# ============================================================================


SerializerFactory = Callable[[], Iterable[str]]


def _serializer_fields(serializer_cls: type[Any]) -> set[str]:
    """Return generated serializer field names as a stable set."""
    return set(serializer_cls().fields.keys())


def _validation_report(
    serializer_cls: type[Any],
    payload: Mapping[str, Any],
) -> ValidationReport:
    """
    Validate one payload through the generated DRF serializer.

    The compiler is responsible for delegating semantic validation to the
    canonical Pydantic schema. DRF only transports the resulting errors.
    """
    serializer = serializer_cls(data=dict(payload))
    is_valid = serializer.is_valid()

    return ValidationReport(
        is_valid=is_valid,
        errors=dict(serializer.errors) if not is_valid else {},
        raw=serializer,
    )


# ============================================================================
# Lab schema/compiler contract
# ============================================================================


@pytest.fixture()
def lab_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(
        lab_schema_expectation("DRF: Lab"),
    )


def test_drf_dependency_exposes_model_serializer() -> None:
    """The optional DRF dependency must expose ModelSerializer."""
    assert drf_serializers.ModelSerializer is not None


def test_drf_available_flag_is_true() -> None:
    """Nova must report DRF availability in this test module."""
    from nova.ecosystem.drf import DRF_AVAILABLE

    assert DRF_AVAILABLE is True


def test_lab_generates_model_serializer() -> None:
    """Nova must compile Lab into a DRF ModelSerializer subclass."""
    serializer_cls = to_drf_serializer(Lab)

    assert issubclass(
        serializer_cls,
        drf_serializers.ModelSerializer,
    )


def test_lab_projection_is_schema_only(
    lab_contract: SchemaCompilerContract,
) -> None:
    """Generated Lab fields must follow the Pydantic projection contract."""
    serializer_cls = to_drf_serializer(Lab)
    fields = _serializer_fields(serializer_cls)

    lab_contract.check_projection(fields)


def test_lab_projection_is_deterministic(
    lab_contract: SchemaCompilerContract,
) -> None:
    """Repeated compilation must produce the same public field projection."""

    def factory() -> Iterable[str]:
        serializer_cls = to_drf_serializer(Lab)
        return serializer_cls().fields.keys()

    lab_contract.check_deterministic_projection(factory)


def test_lab_validation_uses_pydantic(
    lab_contract: SchemaCompilerContract,
) -> None:
    """
    DRF validation must delegate semantic validation to Pydantic.

    Valid payloads are accepted; invalid payloads are returned through DRF's
    error representation.
    """
    serializer_cls = to_drf_serializer(Lab)

    def validate_payload(
        payload: Mapping[str, Any],
    ) -> ValidationReport:
        return _validation_report(serializer_cls, payload)

    lab_contract.check_accepts_valid_payload(validate_payload)
    lab_contract.check_rejects_invalid_payloads(validate_payload)


# ============================================================================
# Schema-only projection contract
# ============================================================================


@pytest.fixture()
def secret_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(
        grant_secret_expectation("DRF: GrantWithSecret"),
    )


def test_secret_field_is_hidden_from_drf(
    secret_contract: SchemaCompilerContract,
) -> None:
    """
    Fields absent from the Pydantic schema must not be exposed by DRF.

    GrantWithSecret.secret_note is intentionally a persistence-only field.
    """
    serializer_cls = to_drf_serializer(GrantWithSecret)
    fields = _serializer_fields(serializer_cls)

    secret_contract.check_projection(fields)

    assert "secret_note" not in fields


# ============================================================================
# Relation projection contract
# ============================================================================


@pytest.fixture()
def relations_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(
        article_relations_expectation("DRF: ArticleWithRelations"),
    )


def test_fk_is_exposed_using_schema_field_name(
    relations_contract: SchemaCompilerContract,
) -> None:
    """
    The compiler must expose `author`, not Django's `author_id` attname.

    This keeps the transport projection aligned with the Pydantic schema.
    """
    serializer_cls = to_drf_serializer(ArticleWithRelations)
    fields = _serializer_fields(serializer_cls)

    relations_contract.check_projection(
        fields,
        use_drf_mapping=True,
    )

    assert "author" in fields
    assert "author_id" not in fields


# ============================================================================
# Error and boundary behavior
# ============================================================================


def test_model_without_schema_raises_value_error() -> None:
    """Schema-less models cannot be compiled into Nova DRF serializers."""
    from tests.models import Article

    with pytest.raises(
        ValueError,
        match="pydantic_schema",
    ):
        to_drf_serializer(Article)


def test_generated_serializer_has_expected_name() -> None:
    """The generated class name must be stable and model-derived."""
    serializer_cls = to_drf_serializer(Lab)

    assert serializer_cls.__name__ == "LabSerializer"


def test_generated_serializer_declares_model_and_fields() -> None:
    """The generated DRF Meta contract must point to the Nova model."""
    serializer_cls = to_drf_serializer(Lab)

    assert serializer_cls.Meta.model is Lab
    assert isinstance(serializer_cls.Meta.fields, list)
    assert serializer_cls.Meta.fields

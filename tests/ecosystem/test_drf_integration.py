"""Tests for Django Rest Framework Auto-Serializer integration."""

from __future__ import annotations

import pytest

# Safe import check for the whole test file
pytest.importorskip("rest_framework", reason="DRF not installed")

from django.db import models
from pydantic import BaseModel, field_validator

from nova import NovaConfig, NovaModel
from nova.ecosystem.drf import to_drf_serializer


# Тестовая Pydantic схема со сложной бизнес-логикой
class FinancialRecordSchema(BaseModel):
    amount: float
    currency: str

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be strictly greater than 0")
        return v


# Тестовая Django модель
class FinancialRecord(NovaModel):
    amount = models.FloatField()
    currency = models.CharField(max_length=3)

    _nova_config = NovaConfig(
        pydantic_schema=FinancialRecordSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class TestDRFAutoSerializer:
    """Test suite for automatic DRF serializer generation."""

    def test_generates_valid_serializer_class(self) -> None:
        """to_drf_serializer must return a valid DRF ModelSerializer class."""
        from rest_framework import serializers

        drf_serializer = to_drf_serializer(FinancialRecord)

        assert issubclass(drf_serializer, serializers.ModelSerializer)
        assert drf_serializer.__name__ == "FinancialRecordSerializer"
        assert drf_serializer.Meta.model == FinancialRecord

    def test_valid_data_passes(self) -> None:
        """Standard valid data must pass DRF validation."""
        drf_serializer = to_drf_serializer(FinancialRecord)
        serializer = drf_serializer(data={"amount": 100.5, "currency": "USD"})

        assert serializer.is_valid()
        assert serializer.validated_data["amount"] == 100.5

    def test_pydantic_validation_overrides_drf(self) -> None:
        """
        KILLER FEATURE TEST:
        Standard DRF allows amount=0.0 (it's a valid FloatField).
        Pydantic rejects it. The auto-serializer MUST reject it using Pydantic rules.
        """
        drf_serializer = to_drf_serializer(FinancialRecord)

        # DRF сам бы пропустил это, но мы заставляем его спросить Pydantic
        serializer = drf_serializer(data={"amount": 0.0, "currency": "USD"})

        assert not serializer.is_valid()
        assert "amount" in serializer.errors
        assert "Amount must be strictly greater than 0" in str(serializer.errors["amount"])

    def test_missing_required_field_fails(self) -> None:
        """Standard DRF required field validation must still work."""
        drf_serializer = to_drf_serializer(FinancialRecord)
        serializer = drf_serializer(data={"amount": 10.0})  # missing currency

        assert not serializer.is_valid()
        assert "currency" in serializer.errors

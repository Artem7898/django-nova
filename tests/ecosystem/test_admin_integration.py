"""Tests for Nova Admin Auto-API schema generation."""

from __future__ import annotations

from django.db import models
from pydantic import BaseModel, field_validator

from nova import NovaConfig, NovaModel
from nova.admin.api import get_admin_schema


class LabSchema(BaseModel):
    name: str
    budget: float

    @field_validator("budget")
    @classmethod
    def check_budget(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Budget cannot be negative")
        return v


class AdminLab(NovaModel):
    name = models.CharField(max_length=200)
    budget = models.FloatField(default=0.0)

    _nova_config = NovaConfig(
        pydantic_schema=LabSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class TestAdminIntegration:
    """Test suite for Auto-Admin schema generation."""

    def test_get_admin_schema_returns_dict(self) -> None:
        schema = get_admin_schema(AdminLab)

        assert isinstance(schema, dict)
        assert "model" in schema
        assert "fields" in schema

    def test_schema_contains_correct_fields(self) -> None:
        schema = get_admin_schema(AdminLab)

        assert "name" in schema["fields"]
        assert schema["fields"]["name"]["type"] == "string"
        assert schema["fields"]["name"]["required"] is True

        assert "budget" in schema["fields"]
        assert schema["fields"]["budget"]["type"] == "number"
        assert schema["fields"]["budget"]["required"] is False

    def test_schema_includes_pydantic_validators(self) -> None:
        schema = get_admin_schema(AdminLab)

        budget_rules = schema["fields"]["budget"].get("validation_rules")

        assert budget_rules is not None

        assert "Budget cannot be negative" in budget_rules

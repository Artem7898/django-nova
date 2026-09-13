"""Django field normalization must precede model-level validation."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.test.utils import isolate_apps
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from nova import NovaConfig, NovaModel
from nova.core.exceptions import NovaValidationError


class BudgetSchema(BaseModel):
    budget: Decimal


@pytest.fixture(params=[True, False], ids=["strict", "non_strict"])
def budget_model(request):
    with isolate_apps():
        observations = []

        class Budget(NovaModel):
            budget = models.DecimalField(max_digits=8, decimal_places=2)

            _nova_config = NovaConfig(
                pydantic_schema=BudgetSchema,
                strict_validation=request.param,
                cache_enabled=False,
            )

            def clean(self):
                observations.append(self.budget)
                assert isinstance(self.budget, Decimal)
                if self.budget < Decimal("0"):
                    raise DjangoValidationError({"budget": "Budget cannot be negative"})

            class Meta:
                app_label = "field_normalization_contract_tests"

        yield Budget, observations, request.param


def prepare_instance(model, value, monkeypatch):
    instance = model(id=1, budget=value)
    unique = Mock()
    constraints = Mock()
    monkeypatch.setattr(instance, "validate_unique", unique)
    monkeypatch.setattr(instance, "validate_constraints", constraints)
    return instance, unique, constraints


@pytest.mark.parametrize("value", ["12.50", Decimal("12.50")], ids=["string", "decimal"])
def test_model_clean_receives_decimal(budget_model, monkeypatch, value):
    model, observations, _ = budget_model
    instance, unique, constraints = prepare_instance(model, value, monkeypatch)

    instance._run_validation()

    assert isinstance(instance.budget, Decimal)
    assert instance.budget == Decimal("12.50")
    assert observations == [Decimal("12.50")]
    unique.assert_called_once_with()
    constraints.assert_called_once_with()


def test_invalid_value_stops_before_model_clean(budget_model, monkeypatch):
    model, observations, strict = budget_model
    instance, unique, constraints = prepare_instance(model, "not-a-number", monkeypatch)

    with pytest.raises(NovaValidationError) as caught:
        instance._run_validation()

    expected_cause = PydanticValidationError if strict else DjangoValidationError
    assert isinstance(caught.value.__cause__, expected_cause)
    assert instance.budget == "not-a-number"
    assert observations == []
    unique.assert_not_called()
    constraints.assert_not_called()


def test_business_rule_receives_normalized_value(budget_model, monkeypatch):
    model, observations, _ = budget_model
    instance, unique, constraints = prepare_instance(model, "-1.00", monkeypatch)

    with pytest.raises(NovaValidationError) as caught:
        instance._run_validation()

    cause = caught.value.__cause__
    assert isinstance(cause, DjangoValidationError)
    assert cause.message_dict == {"budget": ["Budget cannot be negative"]}
    assert observations == [Decimal("-1.00")]
    assert isinstance(instance.budget, Decimal)
    unique.assert_not_called()
    constraints.assert_not_called()

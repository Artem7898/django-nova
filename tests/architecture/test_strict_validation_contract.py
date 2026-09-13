"""Contracts for strict_validation on the NovaModel validation path."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.test.utils import isolate_apps
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from nova import NovaConfig, NovaModel
from nova.core.exceptions import NovaValidationError


class TitleSchema(BaseModel):
    title: str = Field(min_length=5)


@pytest.fixture
def article_model():
    with isolate_apps():

        class ContractArticle(NovaModel):
            title = models.CharField(max_length=100)

            _nova_config = NovaConfig(
                pydantic_schema=TitleSchema,
                strict_validation=True,
                cache_enabled=False,
            )

            class Meta:
                app_label = "strict_validation_contract_tests"

        yield ContractArticle


def test_strict_mode_rejects_pydantic_rule(
    article_model,
    monkeypatch,
) -> None:
    # Valid for Django CharField, invalid for TitleSchema.
    article = article_model(id=1, title="demo")

    clean = Mock()
    unique = Mock()
    constraints = Mock()

    monkeypatch.setattr(article, "clean", clean)
    monkeypatch.setattr(article, "validate_unique", unique)
    monkeypatch.setattr(article, "validate_constraints", constraints)

    with pytest.raises(NovaValidationError) as exc_info:
        article._run_validation()

    cause = exc_info.value.__cause__
    assert isinstance(cause, PydanticValidationError)
    assert cause.errors()[0]["loc"] == ("title",)
    assert cause.errors()[0]["type"] == "string_too_short"

    clean.assert_not_called()
    unique.assert_not_called()
    constraints.assert_not_called()


def test_non_strict_mode_skips_only_pydantic(
    article_model,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        article_model,
        "_nova_config",
        NovaConfig(
            pydantic_schema=TitleSchema,
            strict_validation=False,
            cache_enabled=False,
        ),
    )
    article = article_model(id=1, title="demo")

    pydantic = Mock(
        side_effect=AssertionError("Pydantic must be skipped"),
    )
    clean = Mock()
    unique = Mock()
    constraints = Mock()

    monkeypatch.setattr(article, "to_pydantic", pydantic)
    monkeypatch.setattr(article, "clean", clean)
    monkeypatch.setattr(article, "validate_unique", unique)
    monkeypatch.setattr(article, "validate_constraints", constraints)

    article._run_validation()

    pydantic.assert_not_called()
    clean.assert_called_once_with()
    unique.assert_called_once_with()
    constraints.assert_called_once_with()


def test_non_strict_mode_still_rejects_django_field_error(
    article_model,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        article_model,
        "_nova_config",
        NovaConfig(
            pydantic_schema=TitleSchema,
            strict_validation=False,
            cache_enabled=False,
        ),
    )
    article = article_model(id=1, title="")

    clean = Mock()
    unique = Mock()
    constraints = Mock()

    monkeypatch.setattr(article, "clean", clean)
    monkeypatch.setattr(article, "validate_unique", unique)
    monkeypatch.setattr(article, "validate_constraints", constraints)

    with pytest.raises(NovaValidationError) as exc_info:
        article._run_validation()

    cause = exc_info.value.__cause__
    assert isinstance(cause, DjangoValidationError)
    assert cause.code == "blank"

    clean.assert_not_called()
    unique.assert_not_called()
    constraints.assert_not_called()

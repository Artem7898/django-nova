"""Tests for NovaModel typed mixins."""
from __future__ import annotations
import pytest
from pydantic import BaseModel, ValidationError as PydanticValidationError
from nova.core.exceptions import NovaValidationError
from nova.typing.models import NovaModel, NovaConfig
from nova.validation.pydantic_bridge import generate_pydantic_schema, model_to_pydantic, pydantic_to_model
from tests.models import Article, StrictArticle

class TestTypedModel:
    def test_to_dict_returns_all_fields(self, db: None) -> None:
        article = Article(title="Test Article", body="Test body content", views=42, published=True)
        data = article.to_dict()
        assert data["title"] == "Test Article"
        assert data["views"] == 42

    def test_repr_shows_model_and_pk(self, db: None) -> None:
        article = Article(title="Test")
        assert "tests.Article" in repr(article)

    def test_exclude_from_pydantic(self, db: None) -> None:
        class SecretArticle(NovaModel):
            title = models.CharField(max_length=200)
            secret = models.TextField()
            class Meta:
                app_label = "tests"
            _nova_config = NovaConfig(exclude_from_pydantic=("secret",))

        article = SecretArticle(title="Public", secret="Hidden")
        data = article.to_dict()
        assert "title" in data
        assert "secret" not in data

class TestPydanticBridge:
    def test_generate_schema_has_correct_fields(self) -> None:
        schema = generate_pydantic_schema(Article)
        assert "title" in schema.model_fields
        assert schema.model_fields["title"].is_required()

    def test_generate_schema_charfield_max_length(self) -> None:
        schema = generate_pydantic_schema(Article)
        title_field = schema.model_fields["title"]
        assert title_field.metadata[0].max_length == 200

    def test_model_to_pydantic_valid(self, db: None) -> None:
        article = Article(title="Test", body="Body", views=10)
        schema = model_to_pydantic(article)
        assert schema.title == "Test"
        assert schema.views == 10

    def test_pydantic_to_model_creates_instance(self, db: None) -> None:
        schema = generate_pydantic_schema(Article)
        validated = schema(title="Test", body="Body", views=10)
        article = pydantic_to_model(Article, validated)
        assert article.title == "Test"
        assert article.pk is None

    def test_pydantic_rejects_invalid_type(self) -> None:
        schema = generate_pydantic_schema(Article)
        with pytest.raises(PydanticValidationError):
            schema(title="Test", body="Body", views="not_an_int")

    def test_pydantic_rejects_missing_required(self) -> None:
        schema = generate_pydantic_schema(Article)
        with pytest.raises(PydanticValidationError):
            schema(title="Test")

class TestUnifiedValidation:
    def test_save_with_invalid_data_raises(self, db: None) -> None:
        article = StrictArticle(title="", body="Body")
        with pytest.raises(NovaValidationError):
            article.save()

    def test_save_with_valid_data_passes(self, db: None) -> None:
        article = Article(title="Valid", body="Content")
        article.save()
        assert article.pk is not None

from django.db import models

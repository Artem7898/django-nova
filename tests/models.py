from __future__ import annotations

from django.db import models
from pydantic import BaseModel, field_validator

from nova import NovaConfig, NovaModel


class LabSchema(BaseModel):
    name: str
    budget: float

    @field_validator("budget")
    @classmethod
    def check_budget(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Budget cannot be negative")
        return v


class Lab(NovaModel):
    name = models.CharField(max_length=200)
    budget = models.FloatField(default=0.0)

    _nova_config = NovaConfig(
        pydantic_schema=LabSchema,
        cache_enabled=True,
        strict_validation=True,
    )

    class Meta:
        app_label = "tests"


class Article(NovaModel):
    """Test model for typed operations."""

    title = models.CharField(max_length=200)
    body = models.TextField()
    views = models.IntegerField(default=0)
    published = models.BooleanField(default=False)

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(
        cache_enabled=False,
        strict_validation=True,
    )


class StrictArticle(NovaModel):
    """Test model for strict validation."""

    title = models.CharField(max_length=200)  # blank=False by default
    body = models.TextField()

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(strict_validation=True)


class CachedItem(NovaModel):
    name = models.CharField(max_length=100)
    value = models.IntegerField()

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(cache_enabled=True)


# ========================================================
# 1. FIRST, WE DECLARE THE SCHEMES (Pydantic models)
# ========================================================

class AuthorSchema(BaseModel):
    name: str

class TagSchema(BaseModel):
    name: str

class ArticleWithRelationsSchema(BaseModel):
    title: str
    author: AuthorSchema   # Should trigger select_related
    tags: list[TagSchema]   # Should cause prefetch_related


# ========================================================
# 2. THEN THERE ARE THE DJANGO MODELS THAT USE THEM.
# ========================================================

class Author(NovaModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(pydantic_schema=AuthorSchema)


class Tag(NovaModel):
    name = models.CharField(max_length=50)

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(pydantic_schema=TagSchema)


class ArticleWithRelations(NovaModel):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name='articles')
    tags = models.ManyToManyField(Tag, related_name='articles')

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(
        pydantic_schema=ArticleWithRelationsSchema,
    )
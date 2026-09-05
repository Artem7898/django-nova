from __future__ import annotations

from django.db import models
from pydantic import BaseModel, field_validator

from nova import NovaConfig, NovaModel
from nova.async_orm import AsyncNovaManager

TEST_APP_LABEL = "tests"


# ============================================================================
# Base schemas
# ============================================================================


class LabSchema(BaseModel):
    name: str
    budget: float

    @field_validator("budget")
    @classmethod
    def check_budget(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Budget cannot be negative")
        return value


class CachedItemSchema(BaseModel):
    """Schema used for deterministic validation and cache projection."""

    name: str
    value: int


class AuthorSchema(BaseModel):
    name: str


class TagSchema(BaseModel):
    name: str


class ArticleWithRelationsSchema(BaseModel):
    title: str
    author: AuthorSchema
    tags: list[TagSchema]


class ProfileSchema(BaseModel):
    bio: str


class AuthorDeepSchema(BaseModel):
    name: str
    profile: ProfileSchema


class ArticleDeepSchema(BaseModel):
    title: str
    author: AuthorDeepSchema
    tags: list[TagSchema]


class GrantSchema(BaseModel):
    """Schema with a title validation rule."""

    title: str
    budget: float

    @field_validator("title")
    @classmethod
    def title_min_len(cls, value: str) -> str:
        value = value.strip()

        if len(value) < 5:
            raise ValueError("Title must be at least 5 characters")

        return value


class AsyncArticleSchema(BaseModel):
    title: str
    author: AuthorSchema


class PostSchema(BaseModel):
    title: str
    author: AuthorSchema


class ItemSchema(BaseModel):
    title: str


class HubSchema(BaseModel):
    name: str
    items: list[ItemSchema]


class NodeSchema(BaseModel):
    name: str
    parent: NodeSchema | None = None


class LeftSchema(BaseModel):
    name: str
    right: RightSchema | None = None


class RightSchema(BaseModel):
    name: str
    left: LeftSchema | None = None


class GhostSchema(BaseModel):
    title: str
    author: AuthorSchema


# ============================================================================
# Core test models
# ============================================================================


class Lab(NovaModel):
    name = models.CharField(max_length=200)
    budget = models.FloatField(default=0.0)

    _nova_config = NovaConfig(
        pydantic_schema=LabSchema,
        cache_enabled=True,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Article(NovaModel):
    """Test model for typed operations without schema-based caching."""

    title = models.CharField(max_length=200)
    body = models.TextField()
    views = models.IntegerField(default=0)
    published = models.BooleanField(default=False)

    _nova_config = NovaConfig(
        cache_enabled=False,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class StrictArticle(NovaModel):
    """Test model for strict Django/Pydantic validation behavior."""

    title = models.CharField(max_length=200)
    body = models.TextField()

    _nova_config = NovaConfig(
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class CachedItem(NovaModel):
    """Cache-enabled model with an explicit deterministic schema."""

    name = models.CharField(max_length=100)
    value = models.IntegerField()

    _nova_config = NovaConfig(
        pydantic_schema=CachedItemSchema,
        cache_enabled=True,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


# ============================================================================
# Relation and query-planner models
# ============================================================================


class Author(NovaModel):
    name = models.CharField(max_length=100)

    _nova_config = NovaConfig(
        pydantic_schema=AuthorSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Tag(NovaModel):
    name = models.CharField(max_length=50)

    _nova_config = NovaConfig(
        pydantic_schema=TagSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class ArticleWithRelations(NovaModel):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        related_name="articles",
    )
    tags = models.ManyToManyField(
        Tag,
        related_name="articles",
    )

    _nova_config = NovaConfig(
        pydantic_schema=ArticleWithRelationsSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Profile(NovaModel):
    bio = models.TextField()

    _nova_config = NovaConfig(
        pydantic_schema=ProfileSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class AuthorDeep(NovaModel):
    name = models.CharField(max_length=100)
    profile = models.OneToOneField(
        "tests.Profile",
        on_delete=models.CASCADE,
        related_name="author_profile",
    )

    _nova_config = NovaConfig(
        pydantic_schema=AuthorDeepSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class ArticleDeep(NovaModel):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(
        "tests.AuthorDeep",
        on_delete=models.CASCADE,
        related_name="articles_deep",
    )
    tags = models.ManyToManyField(
        "tests.Tag",
        related_name="articles_deep",
    )

    _nova_config = NovaConfig(
        pydantic_schema=ArticleDeepSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


# ============================================================================
# Ecosystem and validation models
# ============================================================================


class GrantWithSecret(NovaModel):
    """Model with a database field intentionally absent from its schema."""

    title = models.CharField(max_length=300)
    budget = models.FloatField(default=0.0)
    secret_note = models.TextField(
        blank=True,
        default="",
    )

    _nova_config = NovaConfig(
        pydantic_schema=GrantSchema,
        cache_enabled=False,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class AsyncArticle(NovaModel):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        related_name="async_articles",
    )
    objects = AsyncNovaManager()

    _nova_config = NovaConfig(
        pydantic_schema=AsyncArticleSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Post(NovaModel):
    title = models.CharField(max_length=200)
    body = models.TextField()
    views = models.IntegerField(default=0)
    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        related_name="posts",
    )

    _nova_config = NovaConfig(
        pydantic_schema=PostSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


# ============================================================================
# Nested collection and planner models
# ============================================================================


class Hub(NovaModel):
    name = models.CharField(max_length=100)

    _nova_config = NovaConfig(
        pydantic_schema=HubSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Item(NovaModel):
    title = models.CharField(max_length=200)
    hub = models.ForeignKey(
        Hub,
        on_delete=models.CASCADE,
        related_name="items",
    )

    _nova_config = NovaConfig(
        pydantic_schema=ItemSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


# ============================================================================
# Recursive and cyclic planner models
# ============================================================================


class Node(NovaModel):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )

    _nova_config = NovaConfig(
        pydantic_schema=NodeSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Left(NovaModel):
    name = models.CharField(max_length=100)
    right = models.ForeignKey(
        "Right",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="lefts",
    )

    _nova_config = NovaConfig(
        pydantic_schema=LeftSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Right(NovaModel):
    name = models.CharField(max_length=100)
    left = models.ForeignKey(
        "Left",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="rights",
    )

    _nova_config = NovaConfig(
        pydantic_schema=RightSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


class Ghost(NovaModel):
    """Schema references a relation that the model intentionally lacks."""

    title = models.CharField(max_length=100)

    _nova_config = NovaConfig(
        pydantic_schema=GhostSchema,
        strict_validation=True,
    )

    class Meta:
        app_label = TEST_APP_LABEL


# ============================================================================
# Resolve recursive Pydantic references after all schemas are declared.
# ============================================================================


NodeSchema.model_rebuild()
LeftSchema.model_rebuild()
RightSchema.model_rebuild()

"""Small models showing generated schemas and an explicit Pydantic rule."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel, Field

from nova import NovaConfig, NovaModel


class Label(models.Model):
    name = models.CharField(max_length=40)


class Project(NovaModel):
    slug = models.SlugField(max_length=80)
    budget = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    report = models.FileField(upload_to="reports/", blank=True)
    labels = models.ManyToManyField(Label)
    internal_note = models.TextField(blank=True)

    _nova_config = NovaConfig(exclude_from_pydantic=("internal_note",))

    def clean(self) -> None:
        super().clean()
        # NovaModel.save() assigns field.clean() results before this hook.
        if not isinstance(self.budget, Decimal):
            raise ValidationError({"budget": "Expected a normalized Decimal."})
        if self.budget < Decimal("0"):
            raise ValidationError({"budget": "Budget must be non-negative."})


class ArticleSchema(BaseModel):
    title: str = Field(min_length=5, max_length=80)


class Article(NovaModel):
    title = models.CharField(max_length=80)
    _nova_config = NovaConfig(pydantic_schema=ArticleSchema)


class RelaxedArticle(Article):
    _nova_config = NovaConfig(pydantic_schema=ArticleSchema, strict_validation=False)

    class Meta:
        proxy = True

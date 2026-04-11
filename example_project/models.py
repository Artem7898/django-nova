
"""Research data models using django-nova."""

from django.db import models
from nova.typing.models import NovaModel, NovaConfig
from pydantic import BaseModel, field_validator


# 1. Define Pydantic schema for validation (ONE source of truth)
class ResearcherSchema(BaseModel):
    name: str
    email: str
    orcid: str | None = None
    h_index: int = 0

    @field_validator("orcid")
    @classmethod
    def validate_orcid(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("0000-"):
            raise ValueError("ORCID must start with 0000-")
        return v

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h_index cannot be negative")
        return v


# 2. Django model — configuration points to schema
class Researcher(NovaModel):
    name = models.CharField(max_length=300)
    email = models.EmailField(unique=True)
    orcid = models.CharField(max_length=19, blank=True, null=True)
    h_index = models.IntegerField(default=0)
    affiliation = models.ForeignKey(
        "Institution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    _nova_config = NovaConfig(
        pydantic_schema=ResearcherSchema,
        cache_enabled=True,
        cache_ttl_seconds=300,
        strict_validation=True,
    )

    class Meta:
        app_label = "research"


class Institution(NovaModel):
    name = models.CharField(max_length=500)
    ror_id = models.CharField(max_length=9, unique=True)

    _nova_config = NovaConfig(cache_enabled=True)

    class Meta:
        app_label = "research"


class Publication(NovaModel):
    title = models.CharField(max_length=1000)
    doi = models.URLField(unique=True, blank=True, null=True)
    authors = models.ManyToManyField(Researcher, related_name="publications")
    year = models.IntegerField()
    citations = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        cache_enabled=True,
        cache_ttl_seconds=600,
    )

    class Meta:
        app_label = "research"
        indexes = [
            models.Index(fields=["year", "citations"]),
        ]
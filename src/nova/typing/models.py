"""Typed model mixins for Django 5."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import (
    ClassVar,
    Protocol,
    Self,
    TypeVar,
    cast,
    runtime_checkable,
)

from django.db import models
from pydantic import BaseModel

from nova.core.tracing import nova_span
from nova.typing.managers import NovaManager

ModelT = TypeVar("ModelT", bound="NovaModel")


@runtime_checkable
class TypedModelProtocol(Protocol):
    """Protocol for type-checking Nova models."""

    _nova_config: ClassVar[NovaConfig]

    def to_pydantic(self) -> BaseModel: ...
    @classmethod
    def from_pydantic(cls, schema: BaseModel) -> Self: ...
    def to_dict(self) -> dict[str, object]: ...


class NovaConfig:
    """
    Configuration for Nova model behavior.
    """

    __slots__ = (
        "cache_enabled",
        "cache_ttl_seconds",
        "exclude_from_pydantic",
        "pydantic_schema",
        "strict_validation",
    )

    def __init__(
        self,
        *,
        pydantic_schema: type[BaseModel] | None = None,
        cache_enabled: bool = False,
        cache_ttl_seconds: int = 60,
        strict_validation: bool = True,
        exclude_from_pydantic: Sequence[str] = (),
    ) -> None:
        self.pydantic_schema = pydantic_schema
        self.cache_enabled = cache_enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.strict_validation = strict_validation
        self.exclude_from_pydantic = tuple(exclude_from_pydantic)


class NovaModel(models.Model):
    """
    Base model class providing:
    1. Full type inference for fields and QuerySet operations
    2. Automatic Pydantic schema generation
    3. Unified validation (single source of truth)
    4. Smart caching hooks
    """

    _nova_config: ClassVar[NovaConfig] = NovaConfig()

    class Meta:
        abstract = True

    objects: ClassVar[NovaManager[Self]] = NovaManager()  # type: ignore[assignment]

    def save(  # type: ignore[override]
            self,
            force_insert: bool = False,
            force_update: bool = False,
            using: str | None = None,
            update_fields: Sequence[str] | None = None,
    ) -> None:
        """Save with unified validation and deep tracing lifecycle."""
        db = using or self._state.db or "default"

        with nova_span(
                "nova.model.save",
                model=self._meta.label,
                pk=getattr(self, self._meta.pk.attname, None),
                database=db,
                table=self._meta.db_table
        ) as span:
            # 1. Validation Phase
            with nova_span("nova.validation.run", model=self._meta.label) as val_span:
                start_time = time.perf_counter()
                self._run_validation()
                val_time = (time.perf_counter() - start_time) * 1000

                if val_span:
                    val_span.set_attribute("validation.time_ms", val_time)
                    val_span.set_attribute("validation.passed", True)

            # 2. Database Save Phase
            super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )

            if span:
                span.set_attribute("nova.validation.time_ms", val_time)

    def _run_validation(self) -> None:
        """Execute unified validation pipeline."""
        from nova.validation.unified import validate_model_instance

        validate_model_instance(self)

    def to_pydantic(self) -> BaseModel:
        """Convert model instance to Pydantic schema."""
        from nova.validation.pydantic_bridge import model_to_pydantic

        return model_to_pydantic(self)

    @classmethod
    def from_pydantic(cls: type[ModelT], schema: BaseModel) -> ModelT:
        """Create model instance from Pydantic schema."""
        from nova.validation.pydantic_bridge import pydantic_to_model

        return cast(ModelT, pydantic_to_model(cls, schema))

    def to_dict(self) -> dict[str, object]:
        """Serialize to plain dict with proper type coercion."""
        data: dict[str, object] = {}
        for field in self._meta.get_fields():
            if not isinstance(field, models.Field):
                continue
            if not hasattr(field, "attname"):
                continue
            if field.name in self._nova_config.exclude_from_pydantic:
                continue
            value = getattr(self, field.attname, None)
            data[field.name] = value
        return data

    def __repr__(self) -> str:
        opts = self._meta
        pk = getattr(self, opts.pk.attname, None)
        return f"<{opts.label}:{pk}>"
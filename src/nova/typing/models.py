"""Typed model mixins for Django 5.

Provides NovaModel base class with:

- Full type inference for fields and QuerySet operations.
- Automatic Pydantic schema generation.
- Unified validation.
- Smart caching hooks.
- Strict Pyright compatibility.

All Django field access uses nova.typing.django utilities for type safety.
"""

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
from nova.typing.django import get_model_pk
from nova.typing.managers import NovaManager

ModelT = TypeVar(
    "ModelT",
    bound="NovaModel",
)


class NovaConfig:
    """Configuration for Nova model behavior."""

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
        """Initialize Nova model configuration.

        Args:
            pydantic_schema: Optional explicit Pydantic schema.
            cache_enabled: Whether model caching is enabled.
            cache_ttl_seconds: Cache lifetime in seconds.
            strict_validation: Whether Pydantic validation is enabled.
            exclude_from_pydantic: Fields excluded from generated schemas.
        """
        self.pydantic_schema = pydantic_schema
        self.cache_enabled = cache_enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.strict_validation = strict_validation
        self.exclude_from_pydantic = tuple(exclude_from_pydantic)


@runtime_checkable
class TypedModelProtocol(Protocol):
    """Protocol describing the public Nova model boundary."""

    _nova_config: ClassVar[NovaConfig]

    def to_pydantic(self) -> BaseModel:
        """Convert the model to its canonical Pydantic representation."""
        ...


class NovaModel(models.Model):
    """Base Django model for Nova.

    Provides:

    1. Typed model/queryset integration.
    2. Automatic Pydantic schema generation.
    3. Unified validation.
    4. Smart caching integration.
    5. Canonical Django ↔ Pydantic conversion.

    All primary-key metadata access goes through get_model_pk() to maintain
    strict Pyright compatibility.
    """

    _nova_config: ClassVar[NovaConfig] = NovaConfig()
    # Django binds the manager to each concrete model subclass.
    objects: ClassVar[NovaManager[Self]] = NovaManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        abstract = True

    def save(  # type: ignore[override]
        self,
        force_insert: bool = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Sequence[str] | None = None,
    ) -> None:
        """Save the model after running Nova validation."""
        db = using or self._state.db or "default"

        pk_field = get_model_pk(type(self))
        pk_attname: str = pk_field.attname
        pk_value = getattr(
            self,
            pk_attname,
            None,
        )

        with nova_span(
            "nova.model.save",
            model=self._meta.label,
            pk=pk_value,
            database=db,
            table=self._meta.db_table,
        ) as span:
            with nova_span(
                "nova.validation.run",
                model=self._meta.label,
            ) as val_span:
                start_time = time.perf_counter()

                self._run_validation()

                val_time = (time.perf_counter() - start_time) * 1000

                if val_span:
                    val_span.set_attribute(
                        "validation.time_ms",
                        val_time,
                    )
                    val_span.set_attribute(
                        "validation.passed",
                        True,
                    )

            super().save(
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )

            if span:
                span.set_attribute(
                    "nova.validation.time_ms",
                    val_time,
                )

    def _run_validation(self) -> None:
        """Execute Nova's unified validation lifecycle."""
        from nova.validation.unified import (
            validate_model_instance,
        )

        validate_model_instance(self)

    def to_pydantic(self) -> BaseModel:
        """Convert the model using the canonical serialization boundary."""
        from nova.validation.pydantic_bridge import (
            model_to_pydantic,
        )

        return model_to_pydantic(self)

    @classmethod
    def from_pydantic(
        cls: type[ModelT],
        schema: BaseModel,
    ) -> ModelT:
        """Create a Django model from a validated Pydantic schema."""
        from nova.validation.pydantic_bridge import (
            pydantic_to_model,
        )

        return cast(
            ModelT,
            pydantic_to_model(
                cls,
                schema,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical schema-driven dictionary representation.

        The actual serialization implementation lives in
        nova.validation.serialization. This method remains as the public
        NovaModel API for backwards compatibility.
        """
        from nova.validation.pydantic_bridge import (
            generate_pydantic_schema,
        )
        from nova.validation.serialization import (
            model_to_dict,
        )

        schema_cls = getattr(
            self._nova_config,
            "pydantic_schema",
            None,
        )

        if schema_cls is None:
            schema_cls = generate_pydantic_schema(
                model_cls=type(self),
                include_relations=False,
            )

        return model_to_dict(
            self,
            schema_cls=schema_cls,
        )

    def __repr__(self) -> str:
        """Return model label and primary-key value."""
        opts = self._meta

        pk_field = get_model_pk(type(self))
        pk_attname: str = pk_field.attname
        pk_value = getattr(
            self,
            pk_attname,
            None,
        )

        return f"<{opts.label}:{pk_value}>"

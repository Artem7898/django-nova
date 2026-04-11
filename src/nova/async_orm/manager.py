"""
Async Manager for NovaModel.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from django.db import models
from django.db.models import QuerySet

from nova.async_orm.queryset import AsyncTypedQuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class NovaManager(models.Manager):
    """
    Custom manager returning typed async querysets.
    """
    def __init__(self) -> None:
        super().__init__()
        # Re-bind to ensure mypy sees the correct type
        self._queryset_class: type[Any] = QuerySet

    def async_qs(self) -> AsyncTypedQuerySet[ModelT]: # type: ignore
        """Entry point for async queries."""
        return AsyncTypedQuerySet(self.all()) # type: ignore
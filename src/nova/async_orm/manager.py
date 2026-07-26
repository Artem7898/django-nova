"""
Async Manager for NovaModel.
Provides a separate entry point (e.g., Article.aobjects) for async queries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from django.db.models import Manager

from nova.async_orm.queryset import AsyncTypedQuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class AsyncNovaManager(Manager[ModelT]):
    """
    Custom manager returning typed async querysets.

    Usage in models:
        class Article(NovaModel):
            objects = NovaManager()
            aobjects = AsyncNovaManager()
    """
    def __init__(self) -> None:
        super().__init__()
        # Tell Django to use our custom AsyncQuerySet class
        self._queryset_class = AsyncTypedQuerySet

    def get_queryset(self) -> AsyncTypedQuerySet[ModelT]:
        """
        Instantiates the AsyncTypedQuerySet.
        We only pass the model; Django will automatically initialize
        the base async query components.
        """
        return self._queryset_class(model=self.model)
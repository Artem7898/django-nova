"""
Async Manager for NovaModel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db.models import Manager

from nova.async_orm.queryset import AsyncTypedQuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


class AsyncNovaManager[ModelT: NovaModel](Manager[Any]):
    """Custom manager returning typed async querysets."""

    _queryset_class: type[AsyncTypedQuerySet[ModelT]]

    def __init__(self) -> None:
        super().__init__()
        self._queryset_class = AsyncTypedQuerySet

    def get_queryset(self) -> AsyncTypedQuerySet[ModelT]:
        model_cls: type[Any] = self.model
        return self._queryset_class(model=model_cls)

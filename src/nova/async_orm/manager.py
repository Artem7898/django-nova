"""
Async Manager for NovaModel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from nova.async_orm.queryset import AsyncTypedQuerySet
from nova.typing.managers import NovaManager

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class AsyncNovaManager(NovaManager[ModelT]):
    """
    Custom manager returning typed async querysets.
    """
    def get_queryset(self):
        return super().get_queryset()


    def async_qs(self) -> AsyncTypedQuerySet[ModelT]:  # type: ignore
        """Entry point for async queries."""
        return AsyncTypedQuerySet(self.all())  # type: ignore

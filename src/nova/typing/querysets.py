"""
Typed QuerySet wrappers.
Provides generic type safety for Django ORM queries.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, TypeVar

from django.db.models import QuerySet as DjangoQuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class TypedQuerySet[ModelT: "NovaModel"](DjangoQuerySet):
    """
    A strictly typed QuerySet.
    Returns ModelT instead of untyped model instances.
    """
    def __iter__(self) -> Iterator[ModelT]:
        return super().__iter__()

    def __getitem__(self, k: int | slice) -> ModelT | TypedQuerySet[ModelT]:
        return super().__getitem__(k)

    def first(self) -> ModelT | None:
        return super().first()

    def last(self) -> ModelT | None:
        return super().last()
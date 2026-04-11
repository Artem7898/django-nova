"""
True Async QuerySet wrapper.
Django 5.1 added async ORM, but it lacks type safety and caching hooks.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from django.db.models import QuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class AsyncTypedQuerySet:
    """
    Async wrapper around Django QuerySet with Nova caching.
    """
    def __init__(self, qs: QuerySet[ModelT]) -> None:
        self._qs = qs

    def _apply(self, **kwargs: Any) -> AsyncTypedQuerySet[ModelT]:
        return AsyncTypedQuerySet(self._qs.filter(**kwargs))

    async def afirst(self) -> ModelT | None:
        return await self._qs.afirst()

    async def alist(self) -> Sequence[ModelT]:
        # Future: integrate with async cache here
        return [obj async for obj in self._qs]

    async def aexists(self) -> bool:
        return await self._qs.aexists()

    async def acount(self) -> int:
        return await self._qs.acount()

    def __aiter__(self): # type: ignore
        return self._qs.__aiter__()
"""
True Async QuerySet wrapper.
Inherits from Django's native AsyncQuerySet to provide full async ORM compatibility,
type safety, and Schema-Driven query optimization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from django.db.models.query import AsyncQuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class AsyncTypedQuerySet(AsyncQuerySet[ModelT]):
    """
    A strictly typed Async QuerySet.
    Returns ModelT instead of untyped model instances.
    Integrates with the Nova Query Planner via .aauto().
    """

    def aauto(self) -> AsyncTypedQuerySet[ModelT]:
        """
        Async equivalent of .auto().
        Analyzes Pydantic schema and applies select_related/prefetch_related/defer.
        Note: Query planning is a CPU-bound task, so it runs synchronously inside
        the async method to avoid unnecessary event loop overhead.
        """
        from nova.query.planner import build_query_plan

        plan = build_query_plan(self.model)

        if plan.is_empty():
            return self

        qs = self
        # Django's .select_related() and .prefetch_related() clone the QuerySet
        # and return a new instance, even on AsyncQuerySet.
        if plan.select_related:
            qs = qs.select_related(*plan.select_related)
        if plan.prefetch_related:
            qs = qs.prefetch_related(*plan.prefetch_related)
        if plan.defer:
            qs = qs.defer(*plan.defer)

        return qs

    async def alist(self) -> list[ModelT]:
        """
        Convenience method to materialize the async queryset into a list.
        Required for Django < 5.2 compatibility (in 5.2+ .alist() is native).
        """
        return [obj async for obj in self]
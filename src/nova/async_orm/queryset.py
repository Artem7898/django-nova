"""
True Async QuerySet wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.db.models import QuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


class AsyncTypedQuerySet[ModelT: NovaModel](QuerySet[Any]):
    """
    A strictly typed Async QuerySet.
    """

    def aauto(self) -> AsyncTypedQuerySet[ModelT]:
        from nova.query.planner import build_query_plan

        model_cls = cast("type[NovaModel]", self.model)
        plan = build_query_plan(model_cls)

        if plan.is_empty():
            return self

        qs: QuerySet[Any] = self

        if plan.select_related:
            qs = qs.select_related(*plan.select_related)
        if plan.prefetch_related:
            qs = qs.prefetch_related(*plan.prefetch_related)
        if plan.defer:
            qs = qs.defer(*plan.defer)

        return cast("AsyncTypedQuerySet[ModelT]", qs)

    async def alist(self) -> list[ModelT]:
        """Materialize the async queryset into a list."""
        return [cast(ModelT, obj) async for obj in self]

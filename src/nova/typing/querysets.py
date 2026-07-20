"""
Typed QuerySet wrappers.
Provides generic type safety and Schema-Driven optimization hooks.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from django.db.models import QuerySet as DjangoQuerySet

from nova.query.planner import QueryPlan

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

    @contextmanager
    def using_replica(self) -> Iterator[TypedQuerySet[ModelT]]:
        """
        Context manager to route read operations to a database replica.
        """
        from nova.db.router import replica_state

        replica_state.set_read_from_replica()
        try:
            yield self
        finally:
            replica_state.clear_replica_state()

    def get_plan(self) -> QueryPlan:
        """
        Generate a QueryPlan based on the model's Pydantic schema.
        Does NOT execute the plan. Allows inspection or modification.

        Example:
            plan = Article.objects.filter(published=True).get_plan()
            if "author" in plan.select_related:
                plan.select_related.remove("author") # Exclude from join
            Article.objects.filter(published=True).apply_plan(plan)
        """
        from nova.query.planner import build_query_plan
        return build_query_plan(self.model)

    def apply_plan(self, plan: QueryPlan) -> TypedQuerySet[ModelT]:
        """
        Apply a pre-computed or modified QueryPlan to this QuerySet.
        """
        from nova.query.planner import apply_plan as _apply
        return _apply(self, plan)


    def auto(self) -> TypedQuerySet[ModelT]:
        """
        One-click Schema-Driven optimization.
        Analyzes Pydantic schema and immediately applies select_related/prefetch_related.
        """
        from nova.query.planner import apply_optimizations
        return apply_optimizations(self, self.model)
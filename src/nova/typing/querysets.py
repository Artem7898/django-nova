"""Typed QuerySet wrappers.
Provides generic type safety and Schema-Driven optimization hooks.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar, cast

from django.db.models import QuerySet as DjangoQuerySet

from nova.query.planner import QueryPlan, apply_optimizations, apply_plan, build_query_plan

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class TypedQuerySet[ModelT: "NovaModel"](DjangoQuerySet):  # type: ignore[type-arg]
    """
    A strictly typed QuerySet.
    Returns ModelT instead of untyped model instances.
    """

    def __iter__(self) -> Generator[ModelT, None, None]:
        return cast(Generator[ModelT, None, None], super().__iter__())

    def __getitem__(self, k: int | slice) -> ModelT | TypedQuerySet[ModelT]:  # type: ignore[override] - Django QuerySet metaclass conflicts with PEP 695 Generics
        return cast(ModelT | TypedQuerySet[ModelT], super().__getitem__(k))

    def first(self) -> ModelT | None:
        return cast(ModelT | None, super().first())

    def last(self) -> ModelT | None:
        return cast(ModelT | None, super().last())

    @contextmanager
    def using_replica(self) -> Generator[TypedQuerySet[ModelT], None, None]:
        """Context manager to route read operations to a database replica."""
        from nova.db.router import replica_state

        replica_state.set_read_from_replica()
        try:
            yield self
        finally:
            replica_state.clear_replica_state()

    def get_plan(self) -> QueryPlan:
        """Generate a QueryPlan based on the model's Pydantic schema."""
        model_cls: type[ModelT] = self.model  # type: ignore[assignment]
        return build_query_plan(model_cls)

    def apply_plan(self, plan: QueryPlan) -> TypedQuerySet[ModelT]:
        """Apply a pre-computed QueryPlan to this QuerySet."""
        return apply_plan(self, plan)

    def auto(self) -> TypedQuerySet[ModelT]:
        """One-click Schema-Driven optimization."""
        model_cls: type[ModelT] = self.model  # type: ignore[assignment]
        return apply_optimizations(self, model_cls)

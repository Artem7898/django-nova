"""
Query Planner: Analyzes Pydantic schemas to generate a QueryPlan.
Architecture: Schema -> Analyzer -> QueryPlan -> QuerySet
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from django.db import models
from pydantic import BaseModel

from nova.query.relations import find_deep_relations

if TYPE_CHECKING:
    from django.db.models.query import QuerySet

    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)

QS = TypeVar("QS", bound="QuerySet[Any]")


def _empty_str_list() -> list[str]:
    """Explicit factory to prevent list[Unknown] inference in strict mode."""
    return []


@dataclass(frozen=True)
class QueryPlan:
    """Immutable data structure representing database query optimizations."""
    select_related: list[str] = field(default_factory=_empty_str_list)
    prefetch_related: list[str] = field(default_factory=_empty_str_list)
    defer: list[str] = field(default_factory=_empty_str_list)
    only: list[str] = field(default_factory=_empty_str_list)

    def is_empty(self) -> bool:
        """Check if the plan has no optimizations."""
        return not (self.select_related or self.prefetch_related or self.defer or self.only)


def _calculate_deferred_fields(
    model_class: type[NovaModel],
    schema: type[BaseModel],
    select_paths: list[str]
) -> list[str]:
    """Calculates which database columns to DEFER based on the Pydantic schema."""
    django_fields: set[str] = set()
    relation_map: dict[str, str] = {}

    for f in model_class._meta.get_fields():
        if not isinstance(f, models.Field):
            continue
        if f.auto_created or f.many_to_many:
            continue

        attname = f.attname
        name = f.name

        django_fields.add(name)
        django_fields.add(attname)

        if f.is_relation and attname != name:
            relation_map[name] = attname

    needed_fields = set(schema.model_fields.keys())

    for rel_name, rel_attname in relation_map.items():
        if rel_name in needed_fields:
            needed_fields.add(rel_attname)

    for path in select_paths:
        root_rel = path.split("__")[0]
        if root_rel in relation_map:
            needed_fields.add(relation_map[root_rel])

    # SAFETY: NEVER defer the Primary Key
    pk_field = model_class._meta.pk
    needed_fields.add(pk_field.name)
    needed_fields.add(pk_field.attname)

    to_defer = [f for f in django_fields if f not in needed_fields and f not in relation_map.values()]
    return to_defer


def analyze_schema_for_relations(
    schema: type[BaseModel],
    exclude: tuple[str, ...] = ()
) -> dict[str, list[str]]:
    """Entry point for schema analysis. Uses deep graph traversal."""
    visited: set[type[BaseModel]] = set()
    hints = find_deep_relations(schema=schema, visited=visited)

    def _remove_redundant_paths(paths: list[str]) -> list[str]:
        unique_paths = list(set(paths))
        return [p for p in unique_paths if not any(p != other and other.startswith(f"{p}__") for other in unique_paths)]

    # Type is correctly inferred as list[str] now, cast is unnecessary
    raw_select = hints.get("select", [])
    raw_prefetch = hints.get("prefetch", [])

    clean_select = _remove_redundant_paths(raw_select)

    if exclude:
        return {
            "select": [s for s in clean_select if s.split("__")[0] not in exclude],
            "prefetch": [p for p in raw_prefetch if p not in exclude]
        }

    return {
        "select": clean_select,
        "prefetch": raw_prefetch
    }


def build_query_plan(model_class: type[NovaModel]) -> QueryPlan:
    """Main entry point to generate a QueryPlan for a specific model."""
    config = getattr(model_class, '_nova_config', None)

    if not config or not getattr(config, 'pydantic_schema', None):
        return QueryPlan()

    hints = analyze_schema_for_relations(
        schema=config.pydantic_schema,
        exclude=config.exclude_from_pydantic
    )

    deferred = _calculate_deferred_fields(
        model_class=model_class,
        schema=config.pydantic_schema,
        select_paths=hints["select"]
    )

    return QueryPlan(
        select_related=hints["select"],
        prefetch_related=hints["prefetch"],
        defer=deferred
    )


def apply_plan[QS: QuerySet[Any]](queryset: QS, plan: QueryPlan) -> QS:
    """Pure execution function. Applies a QueryPlan to a Django QuerySet."""
    qs = queryset

    if plan.select_related:
        qs = qs.select_related(*plan.select_related)
    if plan.prefetch_related:
        qs = qs.prefetch_related(*plan.prefetch_related)

    if plan.defer:
        qs = qs.defer(*plan.defer)

    return qs


def apply_optimizations[QS: QuerySet[Any]](queryset: QS, model_class: type[NovaModel]) -> QS:
    """Convenience wrapper for backward compatibility."""
    plan = build_query_plan(model_class)
    return apply_plan(queryset, plan)
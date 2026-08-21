"""Query Planner: analyzes Pydantic schemas and generates QueryPlans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from nova.core.exceptions import NovaConfigurationError
from nova.query.relations import find_deep_relations
from nova.typing.django import (
    get_concrete_fields,
    get_model_field,
    get_model_pk,
)

if TYPE_CHECKING:
    from django.db.models.query import QuerySet

    from nova.typing.models import NovaModel


def _empty_str_list() -> list[str]:
    """Create a typed list factory for dataclass defaults."""
    return []


@dataclass(frozen=True)
class QueryPlan:
    """Immutable query optimization plan."""

    select_related: list[str] = field(default_factory=_empty_str_list)
    prefetch_related: list[str] = field(default_factory=_empty_str_list)
    defer: list[str] = field(default_factory=_empty_str_list)
    only: list[str] = field(default_factory=_empty_str_list)

    def is_empty(self) -> bool:
        """Return True when the plan contains no optimizations."""
        return not (self.select_related or self.prefetch_related or self.defer or self.only)

    def explain(self) -> dict[str, tuple[str, ...]]:
        """Return a stable representation of the query plan."""
        return {
            "select_related": tuple(self.select_related),
            "prefetch_related": tuple(self.prefetch_related),
            "only": tuple(self.only),
            "defer": tuple(self.defer),
        }

    def __str__(self) -> str:
        """Return a human-readable representation of the plan."""
        parts = [f"{key}={list(value)}" for key, value in self.explain().items() if value]

        return f"QueryPlan({', '.join(parts) or 'empty'})"


def _calculate_deferred_fields(
    model_class: type[NovaModel],
    schema: type[BaseModel],
    select_paths: list[str],
) -> list[str]:
    """
    Calculate database columns that can safely be deferred.

    Foreign-key attnames and primary-key columns are always preserved.
    """
    django_fields: set[str] = set()
    relation_map: dict[str, str] = {}

    for field_name, model_field in get_concrete_fields(model_class):
        django_fields.add(field_name)
        django_fields.add(model_field.attname)

        if model_field.is_relation and model_field.attname != model_field.name:
            relation_map[model_field.name] = model_field.attname

    needed_fields = set(schema.model_fields)

    # A relation exposed by the Pydantic contract requires its FK column.
    for relation_name, relation_attname in relation_map.items():
        if relation_name in needed_fields:
            needed_fields.add(relation_attname)

    # select_related() also requires the underlying FK column.
    for path in select_paths:
        root_relation = path.split("__", 1)[0]
        relation_attname = relation_map.get(root_relation)

        if relation_attname is not None:
            needed_fields.add(relation_attname)

    # The primary key must never be deferred.
    try:
        pk_field = get_model_pk(model_class)
    except AttributeError as exc:
        raise NovaConfigurationError(
            f"Model {model_class.__name__} has no primary key.",
            setting=f"{model_class.__name__}._meta.pk",
        ) from exc

    needed_fields.add(pk_field.name)
    needed_fields.add(pk_field.attname)

    return sorted(
        field_name
        for field_name in django_fields
        if field_name not in needed_fields and field_name not in relation_map.values()
    )


def _validate_relations_exist(
    model_class: type[NovaModel],
    hints: dict[str, list[str]],
) -> None:
    """Fail fast when a schema references a missing Django relation."""
    for path in [*hints["select"], *hints["prefetch"]]:
        root = path.split("__", 1)[0]

        if get_model_field(model_class, root) is None:
            raise NovaConfigurationError(
                f"Schema contract declares relation '{root}' missing on model "
                f"{model_class.__name__}. Schema contract != ORM contract.",
                setting=f"{model_class.__name__}._nova_config.pydantic_schema",
            )


def _remove_redundant_paths(paths: list[str]) -> list[str]:
    """
    Remove redundant relation paths.

    For example:

        author
        author__profile

    keeps only ``author__profile``.

    The deeper path already establishes the parent relation.
    """
    unique_paths = sorted(set(paths))

    return [
        path
        for path in unique_paths
        if not any(path != other and other.startswith(f"{path}__") for other in unique_paths)
    ]


def analyze_schema_for_relations(
    schema: type[BaseModel],
    exclude: tuple[str, ...] = (),
) -> dict[str, list[str]]:
    """Analyze a Pydantic schema and determine required ORM relations."""
    visited: set[type[BaseModel]] = set()

    hints = find_deep_relations(
        schema=schema,
        visited=visited,
    )

    raw_select = hints.get("select", [])
    raw_prefetch = hints.get("prefetch", [])

    clean_select = _remove_redundant_paths(raw_select)

    if exclude:
        return {
            "select": [path for path in clean_select if path.split("__", 1)[0] not in exclude],
            "prefetch": [path for path in raw_prefetch if path.split("__", 1)[0] not in exclude],
        }

    return {
        "select": clean_select,
        "prefetch": raw_prefetch,
    }


def build_query_plan(model_class: type[NovaModel]) -> QueryPlan:
    """Generate a QueryPlan from the Nova model's Pydantic contract."""
    config = getattr(model_class, "_nova_config", None)

    if config is None:
        return QueryPlan()

    schema = getattr(config, "pydantic_schema", None)

    if schema is None:
        return QueryPlan()

    hints = analyze_schema_for_relations(
        schema=schema,
        exclude=config.exclude_from_pydantic,
    )

    _validate_relations_exist(model_class, hints)

    deferred = _calculate_deferred_fields(
        model_class=model_class,
        schema=schema,
        select_paths=hints["select"],
    )

    return QueryPlan(
        select_related=sorted(hints["select"]),
        prefetch_related=sorted(hints["prefetch"]),
        defer=deferred,
    )


def apply_plan[QS: QuerySet[Any]](
    queryset: QS,
    plan: QueryPlan,
) -> QS:
    """Apply a QueryPlan while preserving the original QuerySet type."""
    qs = queryset

    if plan.select_related:
        qs = qs.select_related(*plan.select_related)

    if plan.prefetch_related:
        qs = qs.prefetch_related(*plan.prefetch_related)

    if plan.defer:
        qs = qs.defer(*plan.defer)

    if plan.only:
        qs = qs.only(*plan.only)

    return qs


def apply_optimizations[QS: QuerySet[Any]](
    queryset: QS,
    model_class: type[NovaModel],
) -> QS:
    """Apply Nova's automatically generated query optimizations."""
    return apply_plan(
        queryset,
        build_query_plan(model_class),
    )

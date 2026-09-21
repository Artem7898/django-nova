"""Contain Django relation introspection used by query cache invalidation.

Supported cache plans use ordinary joins and string prefetch paths on one DB.
Plans whose dependencies cannot be determined are evaluated without caching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import FieldDoesNotExist
from django.db import router
from django.db.models import Manager, Model
from django.db.models.expressions import RawSQL, Subquery
from django.db.models.query import QuerySet
from django.db.models.sql.query import Query
from django.db.models.sql.where import ExtraWhere


class UncacheableQueryError(Exception):
    """The cache cannot safely identify all dependencies of this query plan."""


@dataclass(frozen=True, slots=True)
class QueryDependencies:
    models: tuple[Any, ...]
    prefetches: tuple[str, ...]


def _through_model(relation: Any) -> Any:
    return getattr(relation, "through", None) or getattr(
        getattr(relation, "remote_field", None), "through", None
    )


def related_model_graph(root: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Return a bounded, cycle-safe relation component and its M2M senders.

    Subscribe at configuration time, including plain Django dependencies and
    reverse relations. A writer need not have seen a cached query to notify it.
    The query index and generation vector still use only that query's models.
    """
    pending: list[Any] = [root]
    seen: set[Any] = set()
    through_models: set[Any] = set()
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        if not isinstance(model, type) or not issubclass(model, Model):
            continue
        meta: Any = model._meta
        for relation in meta.get_fields(include_hidden=True):
            target: Any = getattr(relation, "related_model", None)
            if isinstance(target, type) and issubclass(target, Model):
                pending.append(target)
            through = _through_model(relation)
            if isinstance(through, type) and issubclass(through, Model):
                pending.append(through)
                through_models.add(through)
    return tuple(seen), tuple(through_models)


def _check_expression(expression: Any, seen: set[int]) -> None:
    if id(expression) in seen:
        return
    seen.add(id(expression))
    if isinstance(expression, (RawSQL, Subquery, Query, ExtraWhere)):
        raise UncacheableQueryError("Raw SQL and subqueries require explicit dependencies")
    get_sources: Any = getattr(expression, "get_source_expressions", None)
    if callable(get_sources):
        for child in cast("list[Any]", get_sources()):
            _check_expression(child, seen)


def _relation_field(model: Any, part: str) -> Any:
    meta: Any = model._meta
    try:
        return meta.get_field(part)
    except FieldDoesNotExist:
        # Reverse accessor names can differ from ORM filter names (e.g. *_set).
        for relation in meta.get_fields():
            accessor: Any = getattr(relation, "get_accessor_name", None)
            if callable(accessor) and accessor() == part:
                return relation
    raise UncacheableQueryError(f"Unknown prefetch relation: {part}")


def _ordinary_related_managers(model: Any, db: str) -> bool:
    # Custom managers can hide joins/prefetches or depend on instance hints.
    # Keep those queries correct without assuming an incomplete dependency set.
    from nova.typing.managers import NovaManager
    from nova.typing.querysets import TypedQuerySet

    if model._meta.proxy:
        return False
    for manager in (model._base_manager, model._default_manager):
        if manager._db not in (None, db):
            return False
        if type(manager) not in (Manager, NovaManager):
            return False
        if manager._queryset_class not in (QuerySet, TypedQuerySet):
            return False
    return True


def query_dependencies(queryset: object) -> QueryDependencies | None:
    """Collect tables after SQL compilation and relations in prefetch paths."""
    if not isinstance(queryset, QuerySet):
        return None
    qs = cast("Any", queryset)
    query: Any = qs.query
    model: Any = qs.model
    meta: Any = model._meta
    if query.extra or query.extra_tables or query.combined_queries or query.select_for_update:
        raise UncacheableQueryError("This query plan requires uncached execution")
    if meta.proxy:
        raise UncacheableQueryError("Proxy-model signal dependencies are not inferred")
    seen: set[int] = set()
    expressions: list[Any] = [query.where, *query.annotations.values(), *query.select]
    expressions.extend(query.order_by)
    group_by: Any = query.group_by
    if isinstance(group_by, (list, tuple)):
        expressions.extend(cast("list[Any] | tuple[Any, ...]", group_by))
    for expression in expressions:
        _check_expression(expression, seen)

    tables: dict[str, list[Any]] = {}
    for candidate in meta.apps.get_models(include_auto_created=True):
        candidate_meta: Any = candidate._meta
        tables.setdefault(str(candidate_meta.db_table), []).append(candidate)
    # An isolated model need not belong to an installed AppConfig.
    tables.setdefault(str(meta.db_table), []).append(model)
    models: dict[str, Any] = {str(meta.label_lower): model}
    for join in query.alias_map.values():
        table_name = str(join.table_name)
        candidates = tables.get(table_name)
        if not candidates:
            raise UncacheableQueryError(f"Unknown SQL table dependency: {table_name}")
        for candidate in candidates:
            candidate_meta = candidate._meta
            if candidate_meta.proxy:
                raise UncacheableQueryError("Proxy-model signal dependencies are not inferred")
            models[str(candidate_meta.label_lower)] = candidate

    prefetches: list[str] = []
    if qs._prefetch_related_lookups and router.routers:
        raise UncacheableQueryError("Prefetch routing can choose another database")
    for lookup in qs._prefetch_related_lookups:
        if not isinstance(lookup, str):
            # Prefetch can include arbitrary custom querysets, another DB,
            # nested plans, and to_attr. Never collide with ordinary results.
            raise UncacheableQueryError("Custom Prefetch objects use uncached execution")
        prefetches.append(lookup)
        current = model
        for part in lookup.split("__"):
            relation = _relation_field(current, part)
            target = getattr(relation, "related_model", None)
            if not isinstance(target, type) or not issubclass(target, Model):
                raise UncacheableQueryError("Generic/custom prefetch dependencies are unknown")
            if not _ordinary_related_managers(target, str(qs.db)):
                raise UncacheableQueryError("Custom related managers use uncached execution")
            through = _through_model(relation)
            if through is not None:
                models[str(through._meta.label_lower)] = through
            models[str(target._meta.label_lower)] = target
            current = target
    return QueryDependencies(
        models=tuple(models[label] for label in sorted(models)),
        prefetches=tuple(prefetches),
    )

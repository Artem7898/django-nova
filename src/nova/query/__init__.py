"""Schema-Driven Query Optimization API."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "QueryPlan",
    "analyze_schema_for_relations",
    "apply_plan",
    "build_query_plan",
]

def __getattr__(name: str):
    if name == "QueryPlan":
        from nova.query.planner import QueryPlan
        return QueryPlan
    if name == "build_query_plan":
        from nova.query.planner import build_query_plan
        return build_query_plan
    if name == "apply_plan":
        from nova.query.planner import apply_plan
        return apply_plan
    if name == "analyze_schema_for_relations":
        from nova.query.planner import analyze_schema_for_relations
        return analyze_schema_for_relations
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.query.planner import (
        QueryPlan,
        analyze_schema_for_relations,
        apply_plan,
        build_query_plan,
    )

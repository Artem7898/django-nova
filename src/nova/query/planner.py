"""
Query Planner: Analyzes Pydantic schemas to optimize Django ORM queries.
Converts schema relationships into select_related/prefetch_related calls.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def analyze_schema_for_relations(schema: type[BaseModel], exclude: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """
    Inspects a Pydantic schema and returns fields that should be joined.

    Returns:
        Dict with keys 'select' (for ForeignKey/OneToOne) and 'prefetch' (for ManyToMany).
    """
    selects = []
    prefetches = []

    for field_name, field_info in schema.model_fields.items():
        # Skip the excluded fields
        if field_name in exclude:
            continue

        annotation = field_info.annotation

        # List processing (list[SomeModel]) -> prefetch_related
        origin = getattr(annotation, '__origin__', None)
        if origin is list:
            args = getattr(annotation, '__args__', ())
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                prefetches.append(field_name)
            continue

        # Processing of nested models (SomeModel) -> select_related
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            selects.append(field_name)

    return {"select": selects, "prefetch": prefetches}


def apply_optimizations(queryset: Any, model_class: type) -> Any:
    """
    Main entry point. Reads model config, runs analysis, applies to queryset.
    """
    config = getattr(model_class, '_nova_config', None)

    # If there is no configuration or schema, we return it as it is.
    if not config or not config.pydantic_schema:
        return queryset

    hints = analyze_schema_for_relations(
        schema=config.pydantic_schema,
        exclude=config.exclude_from_pydantic
    )

    if hints["select"]:
        queryset = queryset.select_related(*hints["select"])
        logger.debug(
            "Nova Planner: auto-applied select_related(%s) for %s",
            ", ".join(hints["select"]), model_class.__name__
        )

    if hints["prefetch"]:
        queryset = queryset.prefetch_related(*hints["prefetch"])
        logger.debug(
            "Nova Planner: auto-applied prefetch_related(%s) for %s",
            ", ".join(hints["prefetch"]), model_class.__name__
        )

    return queryset
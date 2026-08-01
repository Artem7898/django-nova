"""
Relation Graph Analyzer.
Recursively traverses Pydantic schemas to find nested database relationships.
"""

from __future__ import annotations

import collections.abc
from typing import Any, get_args, get_origin

from pydantic import BaseModel


def _unwrap_core_type(annotation: Any) -> type[Any] | None:
    """Unwraps complex generic annotations to extract the actual inner type class."""
    if annotation is None:
        return None

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is not None and (str(origin) in ("typing.Union", "types.UnionType")):
        for arg in args:
            if arg is not type(None):
                unwrapped = _unwrap_core_type(arg)
                # issubclass is required here because unwrapped is type[Any]
                if unwrapped and issubclass(unwrapped, BaseModel):
                    return unwrapped

    if origin is None and isinstance(annotation, type):
        return annotation

    return None


def find_deep_relations(
    schema: type[BaseModel],
    path_prefix: str = "",
    visited: set[type[BaseModel]] | None = None,
    exclude: set[str] | None = None
) -> dict[str, list[str]]:
    """Recursively analyzes the Pydantic schema to find nested relationships."""
    current_visited: set[type[BaseModel]] = visited if visited is not None else set()
    current_exclude: set[str] = exclude if exclude is not None else set()

    if schema in current_visited:
        return {"select": [], "prefetch": []}

    current_visited.add(schema)

    selects: list[str] = []
    prefetches: list[str] = []

    model_fields = schema.model_fields

    for field_name, field_info in model_fields.items():
        full_field_path = f"{path_prefix}__{field_name}" if path_prefix else field_name
        if field_name in current_exclude or full_field_path in current_exclude:
            continue

        annotation = field_info.annotation
        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in (list, collections.abc.Container, collections.abc.Iterable):
            if args:
                inner_type = _unwrap_core_type(args[0])
                # issubclass is required here because inner_type is type[Any]
                if inner_type and issubclass(inner_type, BaseModel):
                    prefetches.append(field_name)
            continue

        resolved_type = _unwrap_core_type(annotation)
        # issubclass is required here because resolved_type is type[Any]
        if resolved_type and issubclass(resolved_type, BaseModel):
            if resolved_type == schema:
                continue

            selects.append(full_field_path)

            nested_hints = find_deep_relations(
                schema=resolved_type,
                path_prefix=full_field_path,
                visited=current_visited,
                exclude=current_exclude
            )

            selects.extend(nested_hints["select"])
            prefetches.extend(nested_hints["prefetch"])

    return {"select": selects, "prefetch": prefetches}
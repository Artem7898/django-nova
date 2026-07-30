"""
Relation Graph Analyzer.
Recursively traverses Pydantic schemas to find nested database relationships.
"""

from __future__ import annotations

import collections.abc
from typing import TYPE_CHECKING, Any, get_args, get_origin

from pydantic import BaseModel

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo


def _unwrap_core_type(annotation: Any) -> type[Any] | None:
    """
    Unwraps complex generic annotations (e.g., Union, Optional, list)
    to extract the actual inner type class wrapper safely.
    """
    if annotation is None:
        return None

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Handle Nullable types / Unions (e.g., MyModel | None or Optional[MyModel])
    if origin is not None and (str(origin) in ("typing.Union", "types.UnionType")):
        for arg in args:
            if arg is not type(None):
                unwrapped = _unwrap_core_type(arg)
                if unwrapped and issubclass(unwrapped, BaseModel):
                    return unwrapped

    # Handle standard structures
    if origin is None and isinstance(annotation, type):
        return annotation

    return None


def find_deep_relations(
    schema: type[BaseModel],
    path_prefix: str = "",
    visited: set[type[BaseModel]] | None = None,
    exclude: set[str] | None = None
) -> dict[str, list[str]]:
    """
    Recursively analyzes the Pydantic schema to find nested relationships.
    Handles strict typing structures, nullable fields, and modern generic types.
    """
    current_visited = visited if visited is not None else set()
    current_exclude = exclude if exclude is not None else set()

    # 1. Protection against infinite recursion loops
    if schema in current_visited:
        return {"select": [], "prefetch": []}

    current_visited.add(schema)

    selects: list[str] = []
    prefetches: list[str] = []

    # Strict type definition for dict iteration to appease pyright --strict
    model_fields: dict[str, FieldInfo] = schema.model_fields

    for field_name, field_info in model_fields.items():
        # Check explicit path rules matching exclusions
        full_field_path = f"{path_prefix}__{field_name}" if path_prefix else field_name
        if field_name in current_exclude or full_field_path in current_exclude:
            continue

        annotation = field_info.annotation
        origin = get_origin(annotation)
        args = get_args(annotation)

        # 2. Sequence structures (e.g., list[SomeModel] or List[SomeModel]) -> prefetch_related
        if origin in (list, collections.abc.Container, collections.abc.Iterable):
            if args:
                inner_type = _unwrap_core_type(args[0])
                if inner_type and issubclass(inner_type, BaseModel):
                    prefetches.append(field_name)
            continue

        # 3. Direct or Nullable structures -> select_related
        resolved_type = _unwrap_core_type(annotation)
        if resolved_type and issubclass(resolved_type, BaseModel):
            if resolved_type == schema:
                continue

            selects.append(full_field_path)

            # Recursive call to evaluate deep nested linkages safely
            nested_hints = find_deep_relations(
                schema=resolved_type,
                path_prefix=full_field_path,
                visited=current_visited,
                exclude=current_exclude
            )

            selects.extend(nested_hints["select"])
            prefetches.extend(nested_hints["prefetch"])

    return {"select": selects, "prefetch": prefetches}

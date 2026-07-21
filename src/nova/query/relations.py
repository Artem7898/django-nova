"""
Relation Graph Analyzer.
Recursively traverses Pydantic schemas to find nested database relationships.
"""

from __future__ import annotations

from pydantic import BaseModel


def find_deep_relations(
    schema: type[BaseModel],
    path_prefix: str = "",
    visited: set[type[BaseModel]] | None = None,
    exclude: set[str] | None = None
) -> dict[str, list[str]]:
    """
    Recursively analyzes the Pydantic schema to find nested relationships.
    """
    if visited is None:
        visited = set()

    if exclude is None:
        exclude = set()

    # 1. Protection against infinite recursion
    if schema in visited:
        return {"select": [], "prefetch": []}

    visited.add(schema)

    selects = []
    prefetches = []

    for field_name, field_info in schema.model_fields.items():
        # Skip the fields that are in the exclusion list.
        if field_name in exclude:
            continue

        annotation = field_info.annotation
        origin = getattr(annotation, '__origin__', None)

        # 2. list[SomeModel] -> prefetch_related
        if origin is list:
            args = getattr(annotation, '__args__', ())
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                prefetches.append(field_name)
            continue

        # 3. SomeModel -> select_related
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            # Ignoring circular references (when the model refers to itself)
            if annotation == schema:
                continue

            full_path = f"{path_prefix}__{field_name}" if path_prefix else field_name

            selects.append(full_path)

            # Recursive call to find nested links
            nested_hints = find_deep_relations(
                schema=annotation,
                path_prefix=full_path,
                visited=visited,
                exclude=exclude
            )


            selects.extend(nested_hints["select"])
            prefetches.extend(nested_hints["prefetch"])

    return {"select": selects, "prefetch": prefetches}
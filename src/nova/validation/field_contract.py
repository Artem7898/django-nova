"""Canonical field contracts for the Django → Pydantic boundary.

This module defines the semantic representation of a Django model field
used by Nova's schema compiler and serialization layer.

The goal is to prevent Django-specific runtime semantics from leaking into
Pydantic schema generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NovaFieldContract:
    """Canonical semantic description of a Django model field.

    Attributes:
        name: Django field name.
        attname: Django attribute name used for the raw database value.
        python_type: Python type exposed by the field.
        nullable: Whether the database value may be NULL.
        required: Whether the field is required when constructing a schema.
        primary_key: Whether the field is the model primary key.
        has_default: Whether Django provides a default value.
        generated: Whether Django generates the value automatically.
        relation: Whether the field represents a relation.
        many_to_many: Whether the field is a many-to-many relation.
        file_like: Whether the field represents a file/path value.
        max_length: Logical maximum string length, when applicable.
    """

    name: str
    attname: str
    python_type: Any

    nullable: bool = False
    required: bool = True
    primary_key: bool = False
    has_default: bool = False
    generated: bool = False

    relation: bool = False
    many_to_many: bool = False
    file_like: bool = False

    max_length: int | None = None

    @property
    def optional(self) -> bool:
        """Return whether the field may be omitted from input."""
        return not self.required

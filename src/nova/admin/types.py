"""
Typed structures for Nova Admin schemas.
"""

from __future__ import annotations

from typing import TypedDict


class AdminFieldSchema(TypedDict, total=False):
    type: str
    required: bool
    validation_rules: str


class AdminSchema(TypedDict):
    model: str
    fields: dict[str, AdminFieldSchema]

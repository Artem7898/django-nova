"""Django Admin integration based on Pydantic Schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "get_admin_schema",
]

def __getattr__(name: str):
    if name == "get_admin_schema":
        from nova.admin.api import get_admin_schema
        return get_admin_schema
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.admin.api import get_admin_schema
"""Ecosystem Projections: Auto-generation for DRF, FastAPI, Admin, GraphQL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "compile_admin",
    "get_admin_schema",
    "to_drf_serializer",
    "to_fastapi_router",
    "to_strawberry_type",
]

def __getattr__(name: str) -> Any:
    if name == "to_drf_serializer":
        from nova.ecosystem.drf import to_drf_serializer
        return to_drf_serializer
    if name == "to_fastapi_router":
        from nova.ecosystem.fastapi import to_fastapi_router
        return to_fastapi_router
    if name == "get_admin_schema":
        from nova.admin.api import get_admin_schema
        return get_admin_schema
    if name == "compile_admin":
        from nova.admin.api import compile_admin
        return compile_admin
    if name == "to_strawberry_type":
        from nova.ecosystem.graphql import to_strawberry_type
        return to_strawberry_type

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.admin.api import compile_admin, get_admin_schema
    from nova.ecosystem.drf import to_drf_serializer
    from nova.ecosystem.fastapi import to_fastapi_router
    from nova.ecosystem.graphql import to_strawberry_type
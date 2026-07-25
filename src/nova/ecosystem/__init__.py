"""Ecosystem Projections: Auto-generation for DRF, FastAPI, Admin."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "FastAPIBridge",
    "compile_admin",
    "get_admin_schema",
    "to_drf_serializer",
]

def __getattr__(name: str):
    if name == "to_drf_serializer":
        from nova.ecosystem.drf import to_drf_serializer
        return to_drf_serializer
    if name == "FastAPIBridge":
        from nova.ecosystem.fastapi import FastAPIBridge
        return FastAPIBridge
    if name == "get_admin_schema":
        from nova.admin.api import get_admin_schema
        return get_admin_schema
    if name == "compile_admin":
        from nova.admin.api import compile_admin
        return compile_admin

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.admin.api import compile_admin, get_admin_schema
    from nova.ecosystem.drf import to_drf_serializer
    from nova.ecosystem.fastapi import FastAPIBridge
"""Ecosystem Projections: Auto-generation for DRF, FastAPI, Admin."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AutoSerializer",
    "FastAPIBridge",
    "get_admin_schema",
]

def __getattr__(name: str):
    if name == "AutoSerializer":
        from nova.ecosystem.drf import AutoSerializer
        return AutoSerializer
    if name == "FastAPIBridge":
        from nova.ecosystem.fastapi import FastAPIBridge
        return FastAPIBridge
    if name == "get_admin_schema":
        from nova.admin.api import get_admin_schema
        return get_admin_schema
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.admin.api import get_admin_schema
    from nova.ecosystem.drf import AutoSerializer
    from nova.ecosystem.fastapi import FastAPIBridge
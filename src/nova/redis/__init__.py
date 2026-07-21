"""Unified Redis Client (Work in Progress)."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["NovaRedisClient"]

def __getattr__(name: str):
    if name == "NovaRedisClient":
        from nova.redis.client import NovaRedisClient
        return NovaRedisClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.redis.client import NovaRedisClient
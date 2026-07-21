"""Database Infrastructure: Replicas, Routing, Zero-Downtime Migrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AddFieldConcurrently",
    "CreateIndexConcurrently",
    "NovaDatabaseRouter",
    "replica_state",
]

def __getattr__(name: str):
    if name == "NovaDatabaseRouter":
        from nova.db.router import NovaDatabaseRouter
        return NovaDatabaseRouter
    if name == "replica_state":
        from nova.db.router import replica_state
        return replica_state
    if name == "AddFieldConcurrently":
        from nova.db.zero_downtime import AddFieldConcurrently
        return AddFieldConcurrently
    if name == "CreateIndexConcurrently":
        from nova.db.zero_downtime import CreateIndexConcurrently
        return CreateIndexConcurrently
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.db.router import NovaDatabaseRouter, replica_state
    from nova.db.zero_downtime import AddFieldConcurrently, CreateIndexConcurrently
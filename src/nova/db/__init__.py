"""Database Infrastructure: Replicas, Routing, Zero-Downtime Migrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AddFieldConcurrently",
    "CreateIndexConcurrently",
    "NovaDatabaseRouter",
    "chunked_migration",
    "replica_state",
    "report_replica_lag",
]

def __getattr__(name: str):
    if name == "NovaDatabaseRouter":
        from nova.db.router import NovaDatabaseRouter
        return NovaDatabaseRouter
    if name == "replica_state":
        from nova.db.router import replica_state
        return replica_state
    if name == "report_replica_lag":
        from nova.db.router import report_replica_lag
        return report_replica_lag
    if name == "AddFieldConcurrently":
        from nova.db.zero_downtime import AddFieldConcurrently
        return AddFieldConcurrently
    if name == "CreateIndexConcurrently":
        from nova.db.zero_downtime import CreateIndexConcurrently
        return CreateIndexConcurrently
    if name == "chunked_migration":
        from nova.db.splitter import chunked_migration
        return chunked_migration
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.db.router import NovaDatabaseRouter, replica_state, report_replica_lag
    from nova.db.splitter import chunked_migration
    from nova.db.zero_downtime import AddFieldConcurrently, CreateIndexConcurrently

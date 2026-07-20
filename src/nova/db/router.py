"""
Read Replica Database Router for Nova.

Architecture: Uses thread-local state to dynamically route reads to a replica
when explicitly requested via `QuerySet.using_replica()`, without affecting global state.
"""

from __future__ import annotations

import threading
from typing import Any

from nova.conf import nova_settings

# Thread-local storage to keep track of replica state per request/thread
_thread_locals = threading.local()


class ReplicaState:
    """Manages the thread-local flag for replica routing."""

    def set_read_from_replica(self) -> None:
        _thread_locals.use_replica = True

    def clear_replica_state(self) -> None:
        _thread_locals.use_replica = False

    def should_use_replica(self) -> bool:
        return getattr(_thread_locals, "use_replica", False)


# Global state manager instance
replica_state = ReplicaState()


class NovaDatabaseRouter:
    """
    Django Database Router that integrates with Nova's thread-local replica state.

    To activate, add 'nova.db.router.NovaDatabaseRouter' to DATABASES['default']['ROUTER']
    in your Django settings.
    """


    def db_for_read(self, model: Any, **hints: Any) -> str | None:
        """
        Route read operations to replica if explicitly requested via .using_replica().
        Returns None otherwise (falls back to default Django behavior).
        """
        if replica_state.should_use_replica():
            return nova_settings.replica_db_alias
        return None


    def db_for_write(self, model: Any, **hints: Any) -> str | None:
        """
        Route writes to the primary database.
        Also clears the replica state, because after a write, you usually want
        subsequent reads in the same request to hit the primary to avoid replication lag.
        """
        replica_state.clear_replica_state()
        return None


    def allow_relation(self, obj: Any, model1: Any, model2: Any) -> bool | None:
        return None


    def allow_migrate(self, db: str, app_label: str, model_name: str | None = None) -> bool | None:
        return None
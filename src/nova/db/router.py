"""
Read Replica Database Router for Nova.
Architecture: Thread-local state + In-Memory Lag Tracker with Redis backend.
Provides transparent failover to Master if replica lags beyond threshold.
"""

from __future__ import annotations

import threading
import time
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


class _ReplicaLagTracker:
    """
    In-memory tracker for replication lag.
    Prevents Redis network overhead on every database query by using local caching
    and Double-Checked Locking pattern.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lag_ms: float = 0.0
        self._last_check_ts: float = 0.0

    def get_lag(self) -> float:
        """Returns cached lag, updates from Redis if interval has passed."""
        now = time.monotonic()
        interval_sec = nova_settings.replica_lag_check_interval_ms / 1000.0

        # Fast path: no locking if interval hasn't passed
        if (now - self._last_check_ts) >= interval_sec:
            with self._lock:
                # Double-checked locking: ensure only one thread updates the value
                if (now - self._last_check_ts) >= interval_sec:
                    self._last_check_ts = now
                    self._fetch_lag_from_redis()

        return self._lag_ms

    def _fetch_lag_from_redis(self) -> None:
        """Fetches lag from Redis. Fails safely to infinity (forces Master read)."""
        try:
            from nova.redis.client import get_redis_client

            client = get_redis_client()
            # We expect an external monitor (for example, pg_stat_replication) to write a number here
            lag_val = client.get("nova:replica_lag")
            self._lag_ms = float(lag_val) if lag_val is not None else 0.0
        except Exception:
            # FAIL-SAFE: If Redis is unreachable, we assume replica is unhealthy
            # to prevent serving stale data. Route all traffic to Master.
            self._lag_ms = float("inf")

    def is_healthy(self) -> bool:
        """Check if current lag is within acceptable bounds."""
        return self.get_lag() <= nova_settings.replica_max_lag_ms


# Global tracker instance
_lag_tracker = _ReplicaLagTracker()


def report_replica_lag(lag_ms: float) -> None:
    """
    Helper function to update replication lag in Redis.
    Should be called by an external cron job, Celery beat, or Postgres trigger.
    """
    try:
        from nova.redis.client import get_redis_client

        client = get_redis_client()
        # Set with TTL slightly higher than check interval to auto-cleanup if monitor dies
        ttl = max(2, int(nova_settings.replica_lag_check_interval_ms / 1000) + 1)
        client.set("nova:replica_lag", str(lag_ms), ex=ttl)
    except Exception:
        # If we can't report lag, it's a monitoring issue, not critical for the app
        pass


class NovaDatabaseRouter:
    """
    Django Database Router with automatic Lag Awareness.
    Add 'nova.db.router.NovaDatabaseRouter' to DATABASES['default']['ROUTER'].
    """

    def db_for_read(self, model: Any, **hints: Any) -> str | None:
        """
        Routes to replica IF requested via .using_replica() AND lag is acceptable.
        Transparently falls back to Master if replica is lagging.
        """
        if replica_state.should_use_replica() and _lag_tracker.is_healthy():
            return nova_settings.replica_db_alias
        return None

    def db_for_write(self, model: Any, **hints: Any) -> str | None:
        """
        Routes writes to Master.
        Clears thread-local replica state to prevent stale reads in the same request.
        """
        replica_state.clear_replica_state()
        return None

    def allow_relation(self, obj: Any, model1: Any, model2: Any) -> bool | None:
        return None

    def allow_migrate(self, db: str, app_label: str, model_name: str | None = None) -> bool | None:
        return None

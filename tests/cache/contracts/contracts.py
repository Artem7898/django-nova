"""Phase B: Cache backend architectural contracts.

Every synchronous backend must guarantee identical semantics,
conditioned on its declared capabilities (supports_ttl, ...).

Philosophy #3: the application observes ONE contract regardless
of the backend underneath.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

_MISSING = object()

TTL_SECONDS = 0.05
TTL_SLEEP = 0.08


@dataclass(frozen=True, slots=True)
class CacheBackendExpectation:
    target: str
    factory: Callable[[], Any]
    supports_ttl: bool


class CacheBackendContract:
    """Uniform semantic contract for synchronous cache backends."""

    def __init__(self, expectation: CacheBackendExpectation) -> None:
        self.expectation = expectation

    def _new(self) -> Any:
        return self.expectation.factory()

    # -- Core semantics --------------------------------------------------

    def check_set_get_roundtrip(self) -> None:
        backend = self._new()
        backend.set("k", {"a": 1})
        assert backend.get("k") == {"a": 1}

    def check_missing_key_returns_default(self) -> None:
        backend = self._new()
        assert backend.get("nope") is None
        assert backend.get("nope", "dflt") == "dflt"
        assert backend.get("nope", _MISSING) is _MISSING

    def check_overwrite_last_write_wins(self) -> None:
        backend = self._new()
        backend.set("k", 1)
        backend.set("k", 2)
        assert backend.get("k") == 2

    def check_delete_semantics(self) -> None:
        backend = self._new()
        backend.set("k", 1)
        assert backend.delete("k") is True
        assert backend.get("k", _MISSING) is _MISSING
        assert backend.delete("k") is False

    def check_clear(self) -> None:
        backend = self._new()
        backend.set("a", 1)
        backend.set("b", 2)
        backend.clear()
        assert backend.get("a", _MISSING) is _MISSING
        assert backend.get("b", _MISSING) is _MISSING
        assert backend.size() == 0

    # -- Bulk consistency --------------------------------------------------

    def check_bulk_consistency(self) -> None:
        backend = self._new()
        backend.set_many({"x": 1, "y": 2})
        got = backend.get_many(["x", "y", "z"])
        assert got["x"] == 1
        assert got["y"] == 2
        assert got["z"] is None
        assert backend.delete_many(["x", "z"]) == 1
        assert backend.get("x", _MISSING) is _MISSING

    def check_size_consistency(self) -> None:
        backend = self._new()
        assert backend.size() == 0
        backend.set("a", 1)
        backend.set("b", 2)
        assert backend.size() == 2

    # -- Value identity (serialization contract) ----------------------------

    def check_value_identity(self) -> None:
        backend = self._new()
        payload = {
            "decimal": Decimal("1.50"),
            "date": date(2026, 1, 1),
            "list": [1, "two", 3.0],
            "nested": {"ok": True},
        }
        backend.set("k", payload)
        assert backend.get("k") == payload

    # -- TTL semantics (capability-conditioned) ------------------------------

    def check_ttl_semantics(self) -> None:
        backend = self._new()
        if self.expectation.supports_ttl:
            backend.set("k", 1, ttl=TTL_SECONDS)
            assert backend.get("k") == 1
            time.sleep(TTL_SLEEP)
            assert backend.get("k", _MISSING) is _MISSING
            assert backend.delete("k") is False
            backend.set("k2", 1, ttl=timedelta(hours=1))
            assert backend.get("k2") == 1
        else:
            # No-TTL backend: ttl must be silently ignored, value persists.
            backend.set("k", 1, ttl=TTL_SECONDS)
            time.sleep(TTL_SLEEP)
            assert backend.get("k") == 1

    def check_capabilities_declared(self) -> None:
        backend = self._new()
        assert backend.supports_ttl is self.expectation.supports_ttl
        assert isinstance(backend.backend_name, str)

    def run_all(self) -> None:
        self.check_set_get_roundtrip()
        self.check_missing_key_returns_default()
        self.check_overwrite_last_write_wins()
        self.check_delete_semantics()
        self.check_clear()
        self.check_bulk_consistency()
        self.check_size_consistency()
        self.check_value_identity()
        self.check_ttl_semantics()
        self.check_capabilities_declared()

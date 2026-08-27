from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from nova.cache.backends.protocol import CacheBackend

_MISSING = object()

TTL_SECONDS = 0.05
TTL_SLEEP = 0.08


@dataclass(frozen=True, slots=True)
class CacheBackendExpectation:
    """
    Metadata describing a cache backend under contract test.

    ``factory`` must return a fresh backend instance so every contract
    scenario runs against isolated state.
    """

    target: str
    factory: Callable[[], CacheBackend]
    supports_ttl: bool


class CacheBackendContract:
    """
    Canonical behavioral contract for synchronous Nova cache backends.

    Every backend must expose the same observable semantics. Backend-specific
    implementations are allowed internally, but callers must not have to
    know which backend is underneath.

    Subclasses should provide an expectation/factory only. They should not
    override the contract checks.
    """

    def __init__(self, expectation: CacheBackendExpectation) -> None:
        self.expectation = expectation

    @property
    def target(self) -> str:
        """Return the backend identifier used in diagnostics."""
        return self.expectation.target

    def _new(self) -> CacheBackend:
        """Create a fresh isolated backend instance."""
        return self.expectation.factory()

    # ------------------------------------------------------------------
    # Core CRUD semantics
    # ------------------------------------------------------------------

    def check_set_get_roundtrip(self) -> None:
        """A stored value must be returned unchanged."""
        backend = self._new()

        payload = {"a": 1}

        backend.set("key", payload)

        assert backend.get("key") == payload, self.target

    def check_missing_key_returns_default(self) -> None:
        """
        Missing keys must return None or the explicitly supplied default.

        A unique sentinel verifies that the backend does not accidentally
        collapse a caller-provided default into None.
        """
        backend = self._new()

        assert backend.get("missing") is None
        assert backend.get("missing", "default") == "default"
        assert backend.get("missing", _MISSING) is _MISSING

    def check_overwrite_last_write_wins(self) -> None:
        """The latest write must replace the previous value."""
        backend = self._new()

        backend.set("key", 1)
        backend.set("key", 2)

        assert backend.get("key") == 2

    def check_delete_semantics(self) -> None:
        """
        Deleting an existing key returns True.

        Deleting a missing key returns False.
        """
        backend = self._new()

        backend.set("key", 1)

        assert backend.delete("key") is True
        assert backend.get("key", _MISSING) is _MISSING
        assert backend.delete("key") is False

    def check_clear(self) -> None:
        """clear() must remove all stored keys."""
        backend = self._new()

        backend.set("key-a", 1)
        backend.set("key-b", 2)

        backend.clear()

        assert backend.get("key-a", _MISSING) is _MISSING
        assert backend.get("key-b", _MISSING) is _MISSING
        assert backend.size() == 0

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def check_bulk_consistency(self) -> None:
        """
        Bulk operations must have semantics equivalent to their scalar
        counterparts.
        """
        backend = self._new()

        backend.set_many(
            {
                "key-a": 1,
                "key-b": 2,
            },
        )

        result = backend.get_many(
            [
                "key-a",
                "key-b",
                "missing",
            ],
        )

        assert result["key-a"] == 1
        assert result["key-b"] == 2
        assert result["missing"] is None

        deleted = backend.delete_many(
            [
                "key-a",
                "missing",
            ],
        )

        assert deleted == 1
        assert backend.get("key-a", _MISSING) is _MISSING

    # ------------------------------------------------------------------
    # Size / metadata
    # ------------------------------------------------------------------

    def check_size_consistency(self) -> None:
        """size() must accurately describe the current backend state."""
        backend = self._new()

        assert backend.size() == 0

        backend.set("key-a", 1)
        backend.set("key-b", 2)

        assert backend.size() == 2

    def check_backend_metadata(self) -> None:
        """Backend metadata must satisfy the declared protocol."""
        backend = self._new()

        assert isinstance(backend.backend_name, str)
        assert backend.backend_name

        size = backend.size()

        # -1 is the protocol's sentinel for unknown size.
        assert isinstance(size, int)
        assert size >= -1

    def check_capabilities_declared(self) -> None:
        """Capability flags must be real booleans and TTL must match config."""
        backend = self._new()

        assert isinstance(backend.supports_ttl, bool)
        assert isinstance(backend.supports_atomic_increment, bool)
        assert isinstance(backend.supports_pattern_delete, bool)

        assert backend.supports_ttl is self.expectation.supports_ttl

    # ------------------------------------------------------------------
    # Serialization / value semantics
    # ------------------------------------------------------------------

    def check_value_identity(self) -> None:
        """
        Backend serialization must preserve supported Python values.

        The exact representation must round-trip back to the original
        Python object.
        """
        backend = self._new()

        payload = {
            "decimal": Decimal("1.50"),
            "date": date(2026, 1, 1),
            "list": [1, "two", 3.0],
            "nested": {
                "enabled": True,
            },
        }

        backend.set("key", payload)

        assert backend.get("key") == payload

    def check_none_value_semantics(self) -> None:
        """
        Storing None must not behave differently from other values.

        The backend protocol currently uses None as the default cache miss
        result, so callers must use an explicit sentinel when distinguishing
        a stored None from a missing key.
        """
        backend = self._new()

        backend.set("none", None)

        assert backend.get("none") is None
        assert backend.get("none", _MISSING) is None
        assert backend.get("missing", _MISSING) is _MISSING

    # ------------------------------------------------------------------
    # TTL
    # ------------------------------------------------------------------

    def check_ttl_semantics(self) -> None:
        """
        TTL behavior is conditioned on the backend's declared capability.
        """
        backend = self._new()

        if self.expectation.supports_ttl:
            backend.set(
                "short-lived",
                1,
                ttl=TTL_SECONDS,
            )

            assert backend.get("short-lived") == 1

            time.sleep(TTL_SLEEP)

            assert (
                backend.get(
                    "short-lived",
                    _MISSING,
                )
                is _MISSING
            )

            assert backend.delete("short-lived") is False

            backend.set(
                "long-lived",
                1,
                ttl=timedelta(hours=1),
            )

            assert backend.get("long-lived") == 1

            return

        # Backends without TTL support must not pretend that TTL was applied.
        backend.set(
            "persistent",
            1,
            ttl=TTL_SECONDS,
        )

        time.sleep(TTL_SLEEP)

        assert backend.get("persistent") == 1

    # ------------------------------------------------------------------
    # Contract execution
    # ------------------------------------------------------------------

    def run_all(self) -> None:
        """
        Execute the complete synchronous cache backend contract.

        The execution order is explicit by design. Contract tests should not
        depend on reflection, method naming conventions, or test discovery.
        """
        self.check_set_get_roundtrip()
        self.check_missing_key_returns_default()
        self.check_overwrite_last_write_wins()
        self.check_delete_semantics()
        self.check_clear()

        self.check_bulk_consistency()

        self.check_size_consistency()
        self.check_backend_metadata()
        self.check_capabilities_declared()

        self.check_value_identity()
        self.check_none_value_semantics()

        self.check_ttl_semantics()

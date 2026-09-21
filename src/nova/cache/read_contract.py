"""Optional ownership guarantee for synchronous cache reads."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DetachedReadBackend(Protocol):
    """Opt in only when every successful get returns independent mutable data.

    Mutating a returned list, model, JSON value or loaded relation must not
    affect stored data, another read, or a previous caller's result. Immutable
    objects and model classes may be shared. Custom serialization hooks must
    preserve this contract for supported values.

    This is a read guarantee, not permission to skip copying on writes.
    Wrappers must explicitly forward it only if they preserve ownership.
    Missing or non-True declarations keep QuerySetCache's defensive copy.
    """

    @property
    def returns_detached_values(self) -> bool: ...

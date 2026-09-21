"""Optional ownership guarantee for synchronous cache writes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DetachedWriteBackend(Protocol):
    """Opt in when set captures an independent value before returning.

    A successful set must finish copying or serializing the supplied graph
    synchronously. Later changes to that graph must not affect stored data
    or any deferred publication. The backend must not mutate the supplied
    graph, including on failure, or retain mutable input references for
    later use. Immutable bytes and model classes may be shared.

    Custom serialization hooks must honor this contract for supported
    values. A custom backend, serializer or wrapper opts in explicitly only
    after verifying these guarantees. Missing or non-True declarations keep
    QuerySetCache's defensive snapshot.

    This guarantee is independent of returns_detached_values: a detached
    read says nothing about ownership during a write, and vice versa.
    """

    @property
    def stores_detached_values(self) -> bool: ...

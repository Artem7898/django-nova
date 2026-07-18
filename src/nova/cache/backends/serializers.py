"""
Serialization strategies for cache backends.
"""

from __future__ import annotations

import pickle
from typing import Any, Protocol


class CacheSerializer(Protocol):
    def dumps(
        self,
        value: Any,
    ) -> bytes:
        ...

    def loads(
        self,
        value: bytes,
    ) -> Any:
        ...


class PickleSerializer:
    """
    Default serializer for arbitrary Python objects.
    """

    def dumps(
        self,
        value: Any,
    ) -> bytes:
        return pickle.dumps(
            value,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    def loads(
        self,
        value: bytes,
    ) -> Any:
        return pickle.loads(value)
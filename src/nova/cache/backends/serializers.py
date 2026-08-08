"""
Serialization strategies for cache backends.
"""

from __future__ import annotations

import logging
import pickle
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CacheSerializer(Protocol):
    """
    Contract for cache payload serializers.
    """

    def dumps(self, value: Any) -> bytes: ...

    def loads(self, value: bytes) -> Any: ...


class PickleSerializer:
    """
    Default serializer for arbitrary Python objects.

    Implements graceful degradation: corrupted data returns None
    instead of crashing the application.
    """

    def dumps(self, value: Any) -> bytes:
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    def loads(self, value: bytes) -> Any:
        try:
            return pickle.loads(value)
        except Exception:
            logger.warning(
                "Failed to deserialize cache payload, treating as cache miss.",
                exc_info=True,
            )
            return None

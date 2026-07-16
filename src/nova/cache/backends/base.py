from __future__ import annotations

from typing import Any, Protocol


class CacheBackend(Protocol):
    def get(self, key: str) -> Any: ...

    def set(
        self,
        key: str,
        value: Any,
        ttl: int,
    ) -> None: ...

    def delete(self, key: str) -> None: ...

    def clear(self) -> None: ...
"""Celery Task Backend (Stub for future implementation)."""

from __future__ import annotations

from typing import Any

from nova.tasks.exceptions import TaskBackendError


class CeleryBackend:
    """Placeholder for Celery-based task queue."""

    def __init__(self, app_name: str = "nova", **kwargs: Any) -> None:
        raise TaskBackendError(
            "Celery backend is not yet implemented. Please use 'asyncio' backend."
        )

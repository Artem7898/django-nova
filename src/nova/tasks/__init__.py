"""Distributed Task Engine API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "NovaTaskEngine",
    "TaskBackend",
    "TaskResult",
    "nova_task",
]

def __getattr__(name: str) -> Any:
    if name == "nova_task":
        from nova.tasks.decorators import nova_task
        return nova_task
    if name == "NovaTaskEngine":
        from nova.tasks.engine import NovaTaskEngine
        return NovaTaskEngine
    if name == "TaskResult":
        from nova.tasks.models import TaskResult
        return TaskResult
    if name == "TaskBackend":
        from nova.tasks.backends.protocol import TaskBackend
        return TaskBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.tasks.backends.protocol import TaskBackend
    from nova.tasks.decorators import nova_task
    from nova.tasks.engine import NovaTaskEngine
    from nova.tasks.models import TaskResult
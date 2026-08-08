"""
Decorators for task registration.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine
from typing import Any

from nova.tasks.engine import get_engine

TaskFunc = Callable[..., Coroutine[Any, Any, Any]]


def nova_task(name: str | None = None) -> Callable[[TaskFunc], TaskFunc]:
    """
    Marks an async function as a Nova background task.
    """

    def decorator(func: TaskFunc) -> TaskFunc:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            engine = get_engine()
            return engine.submit(func, *args, **kwargs)

        # Safe attribute access for strict type checkers (Callable has no __name__)
        wrapper._nova_task_name = name or getattr(func, "__name__", "unknown")  # type: ignore
        wrapper._is_nova_task = True  # type: ignore
        return wrapper  # type: ignore

    return decorator

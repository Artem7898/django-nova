"""Custom exceptions for the Nova Task engine."""

from __future__ import annotations


class NovaTaskError(Exception):
    """Base exception for nova tasks."""
    pass


class TaskNotFoundError(NovaTaskError):
    """Raised when a task ID does not exist."""
    pass


class TaskBackendError(NovaTaskError):
    """Raised if a specific backend fails to initialize or execute."""
    pass
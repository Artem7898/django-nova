
"""
Nova exception hierarchy.

Design principle: All Nova exceptions wrap Django exceptions
with additional context for debugging and logging.
"""

from __future__ import annotations


class NovaError(Exception):
    """Base exception for all Nova errors."""

    def __init__(self, message: str, *, details: dict[str, str] | None = None) -> None:
        self.details = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        base = super().__str__()
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{base} [{details_str}]"
        return base


class NovaValidationError(NovaError):
    """Raised when unified validation fails."""

    @classmethod
    def from_django(cls, exc: Exception) -> NovaValidationError:
        """Convert Django ValidationError to Nova ValidationError."""
        return cls(str(exc), details={"source": "django"})


class NovaCacheError(NovaError):
    """Raised on cache operation failure."""

    def __init__(self, message: str, *, cache_key: str | None = None) -> None:
        details = {}
        if cache_key:
            details["cache_key"] = cache_key[:16]
        super().__init__(message, details=details)


class NovaConfigurationError(NovaError):
    """Raised on incorrect Nova configuration."""

    def __init__(self, message: str, *, setting: str | None = None) -> None:
        details = {}
        if setting:
            details["setting"] = setting
        super().__init__(message, details=details)


class NovaAsyncError(NovaError):
    """Raised on async operation failure."""

    def __init__(self, message: str, *, operation: str | None = None) -> None:
        details = {}
        if operation:
            details["operation"] = operation
        super().__init__(message, details=details)


class NovaWarning(Warning):
    """Warning for non-fatal Nova issues."""

    pass
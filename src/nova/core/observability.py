from __future__ import annotations

import logging
from typing import Any

# --- Safe Import Block (Architecture Freeze compliant) ---
try:
    import structlog
    _structlog_available = True
except ImportError:
    # Pyright stub to prevent cascade `None` type failures
    structlog = None  # type: ignore[assignment, misc]
    _structlog_available = False


def setup_nova_logging() -> None:
    """Configure structured logging if structlog is installed."""
    if not _structlog_available:
        logging.basicConfig(level=logging.INFO)
        return

    structlog.configure(  # type: ignore[union-attr]
        processors=[
            structlog.contextvars.merge_contextvars,  # type: ignore[union-attr]
            structlog.processors.add_log_level,  # type: ignore[union-attr]
            structlog.processors.TimeStamper(fmt="iso"),  # type: ignore[union-attr]
            structlog.processors.StackInfoRenderer(),  # type: ignore[union-attr]
            structlog.dev.set_exc_info,  # type: ignore[union-attr]
            structlog.processors.JSONRenderer(),  # type: ignore[union-attr]
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),  # type: ignore[union-attr]
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),  # type: ignore[union-attr]
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return structlog logger if available, otherwise fallback to stdlib logging."""
    if _structlog_available:
        if structlog.is_configured():  # type: ignore[union-attr]
            return structlog.get_logger(name)  # type: ignore[union-attr]
        return structlog.stdlib.get_logger(name)  # type: ignore[union-attr]

    return logging.getLogger(name)
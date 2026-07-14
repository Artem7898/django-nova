from __future__ import annotations

import logging
from typing import Any

try:
    import structlog

    STRUCTLOG_AVAILABLE = True
except ImportError:
    structlog = None  # type: ignore[assignment]
    STRUCTLOG_AVAILABLE = False


def setup_nova_logging() -> None:
    """
    Configure structured logging if structlog is installed.
    """
    if not STRUCTLOG_AVAILABLE:
        logging.basicConfig(level=logging.INFO)
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """
    Return structlog logger if available,
    otherwise fallback to stdlib logging.
    """
    if STRUCTLOG_AVAILABLE:
        if structlog.is_configured():
            return structlog.get_logger(name)
        return structlog.stdlib.get_logger(name)

    return logging.getLogger(name)
